import logging
from random import randint
from typing import List

import numpy as np
from tqdm import tqdm

from dbpunctuator.utils import NORMAL_TOKEN_TAG

PAD_TOKEN = "[PAD]"
logger = logging.getLogger(__name__)


def read_data(file_path, min_sequence_length, max_sequence_length) -> List[List]:
    def read_line(text_line):
        return text_line.strip().split("\t")

    token_docs = []
    tag_docs = []
    line_index = 0

    token_doc = []
    tag_doc = []
    with open(file_path, "r") as data_file:
        pbar = tqdm(data_file.readlines())
        for line in pbar:
            if line_index == 0:
                token_doc = []
                tag_doc = []
                target_sequence_length = randint(
                    min_sequence_length, max_sequence_length
                )
            processed_line = read_line(line)
            try:
                token_doc.append(processed_line[0])
                tag_doc.append(processed_line[1])
            except IndexError:
                logger.warning(f"ignore the bad line: {line}")
                continue
            line_index += 1
            if line_index == target_sequence_length:
                token_docs.append(token_doc)
                tag_docs.append(tag_doc)
                line_index = 0
                pbar.update(target_sequence_length)
        token_doc += [PAD_TOKEN] * (target_sequence_length - line_index)
        tag_doc += [NORMAL_TOKEN_TAG] * (target_sequence_length - line_index)
        token_docs.append(token_doc)
        tag_docs.append(tag_doc)

        pbar.close()

    return token_docs, tag_docs


def generate_tag_ids(tag_docs):
    unique_tags = set([tag for tags in tag_docs for tag in tags])
    tag2id = {tag: id for id, tag in enumerate(unique_tags)}
    id2tag = {id: tag for tag, id in tag2id.items()}

    return tag2id, id2tag


def unison_shuffled_copies(a, b):
    assert len(a) == len(b)
    p = np.random.permutation(len(a))
    return a[p].tolist(), b[p].tolist()


def train_test_split(tokens, tags, test_size=0.2, shuffle=True):
    if shuffle:
        tokens, tags = unison_shuffled_copies(
            np.array(tokens, dtype=object), np.array(tags, dtype=object)
        )
    index = round(len(tokens) * (1 - test_size))
    return tokens[:index], tokens[index:], tags[:index], tags[index:]
