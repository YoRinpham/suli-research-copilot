from pathlib import Path
from uuid import uuid4


class LocalPDFStorage:
    def __init__(self, root: Path):
        self.root = root.resolve()

    def path_for(self, key: str) -> Path:
        if len(key) != 36 or not key.endswith(".pdf") or not all(
            character in "0123456789abcdef" for character in key[:-4]
        ):
            raise ValueError("Invalid storage key")
        return self.root / key

    def save(self, data: bytes) -> str:
        self.root.mkdir(parents=True, exist_ok=True)
        key = f"{uuid4().hex}.pdf"
        with self.path_for(key).open("xb") as output:
            output.write(data)
        return key

    def read(self, key: str) -> bytes:
        return self.path_for(key).read_bytes()

    def remove(self, key: str) -> None:
        self.path_for(key).unlink(missing_ok=True)
