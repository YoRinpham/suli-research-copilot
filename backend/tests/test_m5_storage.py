from hashlib import sha256

import pytest

from app.services.m5_storage import M5Storage


def test_storage_uses_controlled_immutable_keys_and_verifies_hash(tmp_path):
    storage = M5Storage(tmp_path)
    key, digest = storage.save("datasets", b"a\n1\n", "csv")
    assert key.startswith("datasets/") and key.endswith(".csv")
    assert digest == sha256(b"a\n1\n").hexdigest()
    assert storage.read(key, digest) == b"a\n1\n"
    with pytest.raises(FileExistsError):
        storage.save_at_key(key, b"different")
    with pytest.raises(ValueError):
        storage.read("../secret", digest)
