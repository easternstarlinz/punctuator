from dbpunctuator.training import TrainingArguments, TrainingPipeline
from dbpunctuator.utils.utils import register_logger

if __name__ == "__main__":
    register_logger()

    training_args = TrainingArguments(
        data_file_path="training_data/english_token_tag_data.txt",
        model_name="distilbert-base-uncased",
        tokenizer_name="distilbert-base-uncased",
        split_rate=0.2,
        min_sequence_length=100,
        max_sequence_length=200,
        epoch=10,
        batch_size=32,
        model_storage_dir="models/english_punctuator_2",
        addtional_model_config={"dropout": 0.2},
    )

    training_pipeline = TrainingPipeline(training_args)
    training_pipeline.run()