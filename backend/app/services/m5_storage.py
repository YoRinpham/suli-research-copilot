"""Controlled immutable local storage for CSV versions and figure artifacts."""

from hashlib import sha256
from pathlib import Path
from uuid import uuid4


class M5Storage:
    def __init__(self, root: Path):
        self.root = root.resolve()

    def _path(self, key: str) -> Path:
        parts = key.split("/")
        if (len(parts) != 2 or parts[0] not in {"datasets", "figures"} or not parts[1] or
                "/" in parts[1] or "\\" in parts[1] or ".." in parts[1]):
            raise ValueError("Invalid storage key")
        path = (self.root / parts[0] / parts[1]).resolve()
        if self.root not in path.parents:
            raise ValueError("Storage path escapes the controlled root")
        return path

    def save(self, kind: str, payload: bytes, extension: str) -> tuple[str, str]:
        if kind not in {"datasets", "figures"} or extension not in {"csv", "png"}:
            raise ValueError("Invalid artifact type")
        key = f"{kind}/{uuid4()}.{extension}"
        self.save_at_key(key, payload)
        return key, sha256(payload).hexdigest()

    def save_at_key(self, key: str, payload: bytes) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        created = False
        try:
            with path.open("xb") as stream:
                created = True
                stream.write(payload)
        except Exception:
            if created:
                path.unlink(missing_ok=True)
            raise

    def read(self, key: str, expected_hash: str) -> bytes:
        payload = self._path(key).read_bytes()
        if sha256(payload).hexdigest() != expected_hash:
            raise ValueError("Stored artifact hash does not match its metadata")
        return payload

    def discard(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)

    def discard_figure(self, key: str) -> None:
        if not key.startswith("figures/") or not key.endswith(".png"):
            raise ValueError("Invalid figure storage key")
        path = self._path(key)
        if (self.root / "figures") not in path.parents:
            raise ValueError("Figure path escapes the figure directory")
        path.unlink(missing_ok=True)
