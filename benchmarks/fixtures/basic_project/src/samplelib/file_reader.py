from pathlib import Path


def read_text(file_path):
    return Path(file_path).read_text(encoding="utf-8")
