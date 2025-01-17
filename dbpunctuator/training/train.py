import logging
import time
from typing import Dict, Optional

import numpy as np
import torch
from pydantic import BaseModel
from sklearn.model_selection import train_test_split
from torch._C import device  # noqa: F401
from torch.utils.data import DataLoader
from tqdm import tqdm
from transformers import (
    AdamW,
    DistilBertConfig,
    DistilBertForTokenClassification,
    DistilBertTokenizerFast,
)

from .dataset import PunctuatorDataset, generate_tag_ids, read_data

logger = logging.getLogger(__name__)


class TrainingArguments(BaseModel):
    """Arguments pertaining to which model/config/tokenizer we are going to fine-tune from.

    Args:
        data_file_path(str): path of training data
        model_name_or_path(str): name or path of pre-trained model
        tokenizer_name(str): name of pretrained tokenizer
        split_rate(float): train and validation split rate
        min_sequence_length(int): min sequence length of one sample
        max_sequence_length(int): max sequence length of one sample
        epoch(int): number of epoch
        batch_size(int): batch size
        model_storage_dir(str): fine-tuned model storage path
        addtional_model_config(Optional[Dict]): additional configuration for model
        early_stop_count(int): after how many epochs to early stop training if valid loss not become smaller. default 3 # noqa: E501
    """

    data_file_path: str
    model_name_or_path: str
    tokenizer_name: str
    split_rate: float
    min_sequence_length: int
    max_sequence_length: int
    epoch: int
    batch_size: int
    model_storage_dir: str
    best_state_dict:Optional[Dict]
    addtional_model_config: Optional[Dict]
    early_stop_count: Optional[int] = 3


class TrainingPipeline:
    def __init__(self, training_arguments):
        """Training pipeline for fine-tuning the distilbert token classifier for punctuation

        Args:
            training_arguments (TrainingArguments): arguments passed to training pipeline
        """
        self.arguments = training_arguments
        self.device = (
            torch.device("cuda") if torch.cuda.is_available() else torch.device("cpu")
        )

    def load_training_data(self):
        logger.info("load training data")
        texts, tags = read_data(
            self.arguments.data_file_path,
            self.arguments.min_sequence_length,
            self.arguments.max_sequence_length,
        )
        logger.info(f"data sample: {texts[0]}")
        (
            self.train_texts,
            self.val_texts,
            self.train_tags,
            self.val_tags,
        ) = train_test_split(texts, tags, test_size=self.arguments.split_rate)
        self.tag2id, self.id2tag = generate_tag_ids(tag_docs=tags)

        return self

    def tokenize(self):
        logger.info("tokenize data")
        self.model_config = DistilBertConfig.from_pretrained(
            self.arguments.model_name_or_path,
            label2id=self.tag2id,
            id2label=self.id2tag,
            num_labels=len(self.tag2id),
            **self.arguments.addtional_model_config,
        )
        tokenizer = DistilBertTokenizerFast.from_pretrained(
            self.arguments.tokenizer_name
        )
        self.train_encodings = tokenizer(
            self.train_texts,
            is_split_into_words=True,
            return_offsets_mapping=True,
            padding=True,
        )
        self.val_encodings = tokenizer(
            self.val_texts,
            is_split_into_words=True,
            return_offsets_mapping=True,
            padding=True,
        )
        self.train_labels = self._encode_tags(self.train_tags, self.train_encodings)
        self.val_labels = self._encode_tags(self.val_tags, self.val_encodings)

        return self

    def generate_dataset(self):
        logger.info("generate dataset from tokenized data")
        self.train_encodings.pop("offset_mapping")
        self.val_encodings.pop("offset_mapping")
        self.training_dataset = PunctuatorDataset(
            self.train_encodings, self.train_labels
        )
        self.val_dataset = PunctuatorDataset(self.val_encodings, self.val_labels)

        return self

    def fine_tune(self):
        logger.info("start fine tune")
        self.classifier = DistilBertForTokenClassification.from_pretrained(
            self.arguments.model_name_or_path,
            config=self.model_config,
        )
        self.classifier.to(self.device)

        train_loader = DataLoader(
            self.training_dataset, batch_size=self.arguments.batch_size, shuffle=True
        )
        val_loader = DataLoader(
            self.val_dataset, batch_size=self.arguments.batch_size, shuffle=True
        )
        optim = AdamW(self.classifier.parameters(), lr=5e-5)

        best_valid_loss = 1
        no_improvement_count = 0
        with tqdm(total=self.arguments.epoch) as pbar:
            for epoch in range(self.arguments.epoch):
                pbar.set_description(f"Processing epoch: {epoch + 1}")

                start_time = time.time()

                self.classifier.train()

                train_loss, train_acc = self._train(train_loader, optim)
                val_loss, val_acc = self._train(val_loader, optim, True)

                end_time = time.time()

                epoch_mins, epoch_secs = self._epoch_time(start_time, end_time)

                logger.info(
                    f"Epoch: {epoch + 1:02} | Epoch Time: {epoch_mins}m {epoch_secs}s"
                )
                logger.info(
                    f"\tTrain Loss: {train_loss:.3f} | Train Acc: {train_acc * 100:.2f}%"
                )
                logger.info(
                    f"\t Val. Loss: {val_loss:.3f} |  Val. Acc: {val_acc * 100:.2f}%"
                )
                pbar.update(1)

                if val_loss < best_valid_loss:
                    best_valid_loss = val_loss
                    self.best_state_dict = self.classifier.state_dict()
                else:
                    no_improvement_count += 1
                    if no_improvement_count >= self.arguments.early_stop_count:
                        logger.info(
                            f"No improvement for past {no_improvement_count} epochs, early stop training."
                        )
                        return self

            logger.info("fine-tune finished")

        return self

    def persist(self):
        """Persist this model into the passed directory."""

        logger.info("persist fine-tuned model")

        self.classifier.load_state_dict(self.best_state_dict)
        self.classifier.save_pretrained(self.arguments.model_storage_dir)

        logger.info(f"fine-tuned model stored to {self.arguments.model_storage_dir}")

    def _encode_tags(self, tags, encodings):
        logger.info("encoding tags")
        labels = [[self.tag2id[tag] for tag in doc] for doc in tags]
        encoded_labels = []
        with tqdm(total=len(labels)) as pbar:
            for doc_labels, doc_offset in zip(labels, encodings.offset_mapping):
                try:
                    # create an empty array of -100
                    doc_enc_labels = np.ones(len(doc_offset), dtype=int) * -100
                    arr_offset = np.array(doc_offset)

                    # set labels whose first offset position is 0 and the second is not 0
                    doc_enc_labels[
                        (arr_offset[:, 0] == 0) & (arr_offset[:, 1] != 0)
                    ] = doc_labels
                    encoded_labels.append(doc_enc_labels.tolist())
                except ValueError as e:
                    logger.warning(f"error encoding: {str(e)}")
                pbar.update(1)

        return encoded_labels

    def _train(self, iterator, optim, is_val=False):
        epoch_loss = 0
        epoch_acc = 0
        if is_val:
            self.classifier.train(False)
        else:
            self.classifier.train()

        steps = 0
        with tqdm(total=len(iterator)) as pbar:
            for batch in iterator:
                steps += 1
                pbar.set_description(f"Processing batch: {steps}")

                optim.zero_grad()
                input_ids = batch["input_ids"].to(self.device)
                attention_mask = batch["attention_mask"].to(self.device)
                labels = batch["labels"].to(self.device)
                outputs = self.classifier(
                    input_ids, attention_mask=attention_mask, labels=labels
                )
                loss = outputs.loss
                logits = outputs.logits

                epoch_loss += loss.item()
                epoch_acc += self._accuracy(logits, attention_mask, labels)

                if not is_val:
                    loss.backward()
                    optim.step()

                pbar.update(1)
                pbar.set_postfix(
                    {
                        "Last_loss": f"{loss:.2f}",
                        "Avg_cum_loss": f"{epoch_loss/steps:.2f}",
                    }
                )

        return epoch_loss / steps, epoch_acc / steps

    def _epoch_time(self, start_time, end_time):
        elapsed_time = end_time - start_time
        elapsed_mins = int(elapsed_time // 60)
        elapsed_secs = int(elapsed_time - (elapsed_mins * 60))
        return elapsed_mins, elapsed_secs

    def _accuracy(self, logits, attention_mask, labels):
        if self.device.type == "cuda":
            max_preds = logits.argmax(dim=2).detach().cpu().numpy().flatten()
            flattened_labels = labels.detach().cpu().numpy().flatten()
            flattened_attention = attention_mask.detach().cpu().numpy().flatten()
        else:
            max_preds = logits.argmax(dim=2).detach().numpy().flatten()
            flattened_labels = labels.detach().numpy().flatten()
            flattened_attention = attention_mask.detach().numpy().flatten()

        not_padding_labels = flattened_labels[flattened_attention == 1]
        not_padding_preds = max_preds[flattened_attention == 1]
        reduce_ignored = not_padding_labels >= 0
        true_labels = not_padding_labels[reduce_ignored]  # remove ignored -100
        true_preds = not_padding_preds[reduce_ignored]

        if true_preds.shape[0] == true_labels.shape[0]:
            return np.sum(true_preds == true_labels) / true_preds.shape[0]

        return 0

    def run(self):
        self.load_training_data().tokenize().generate_dataset().fine_tune().persist()
