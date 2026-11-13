"""Tải model một lần trước khi chạy; không được gọi tự động trong parser."""

import os
import argparse
import subprocess
import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

SAT_REVISION = "137da054051ad9f1eac42025f758db4ac9f22535"
TOKENIZER_REVISION = "e73636d4f797dec63c3081bb6ed5c7b0bb3f2089"
SPACY_MODEL_VERSION = "3.8.0"
SPACY_MODEL_URL = (
    "https://github.com/explosion/spacy-models/releases/download/"
    "en_core_web_sm-3.8.0/en_core_web_sm-3.8.0-py3-none-any.whl"
)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", choices=["spacy", "sat"], default="spacy")
    args = parser.parse_args(argv)
    if args.backend == "spacy":
        try:
            ready = version("en-core-web-sm") == SPACY_MODEL_VERSION
        except PackageNotFoundError:
            ready = False
        if not ready:
            # Chỉ lệnh setup chủ động mới gọi pip; model ghim đúng bản đã benchmark.
            subprocess.run([sys.executable, "-m", "pip", "install", SPACY_MODEL_URL], check=True)
        print(f"Ready: en_core_web_sm {SPACY_MODEL_VERSION}")
        return

    # Không cần cài Hugging Face/ONNX khi chỉ sử dụng spaCy mặc định.
    from huggingface_hub import snapshot_download

    # Pin revision để lần chạy sau không đổi trọng số/tokenizer ngoài ý muốn.
    for repo, revision, directory, patterns in [
        ("segment-any-text/sat-3l-sm", SAT_REVISION,
         os.environ.get("ARXIV_SAT_MODEL", "data/models/sat-3l-sm"),
         ["config.json", "model_optimized.onnx"]),
        ("facebookAI/xlm-roberta-base", TOKENIZER_REVISION,
         os.environ.get("ARXIV_SAT_TOKENIZER", "data/models/xlm-roberta-base-tokenizer"),
         ["config.json", "tokenizer.json", "tokenizer_config.json", "sentencepiece.bpe.model", "special_tokens_map.json"]),
    ]:
        destination = Path(directory).resolve()
        snapshot_download(repo, revision=revision, allow_patterns=patterns, local_dir=str(destination))
        print(f"Ready: {destination}")


if __name__ == "__main__":
    main()
