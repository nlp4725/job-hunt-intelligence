"""Where uploaded resumes live: encrypted, under a per-user key.

LocalFileStore is for development and tests; an S3 store with the same
put/get takes its place in AWS (productization plan, phase 9).
"""

import os
import re
from pathlib import Path, PurePath

from cryptography.fernet import Fernet


class ResumeCipher:
    """Symmetric encryption (Fernet: AES-128-CBC + HMAC-SHA256) for resume files
    and their text. The key comes from JHI_RESUME_KEY; in AWS it moves to
    Secrets Manager / KMS."""

    def __init__(self, key: bytes | str):
        self._fernet = Fernet(key)

    @classmethod
    def from_env(cls) -> "ResumeCipher":
        key = os.environ.get("JHI_RESUME_KEY")
        if not key:
            raise RuntimeError(
                "Set JHI_RESUME_KEY. Generate one with: "
                "python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'"
            )
        return cls(key)

    def encrypt(self, data: bytes) -> bytes:
        return self._fernet.encrypt(data)

    def decrypt(self, token: bytes) -> bytes:
        return self._fernet.decrypt(token)


def resume_storage_key(user_id: int, version: int, filename: str) -> str:
    """Only the file's own name survives, reduced to safe characters, so an
    uploaded name can never point outside the user's folder."""
    name = re.sub(r"[^\w.-]", "_", PurePath(filename).name)
    if name in ("", ".", ".."):
        name = "resume"
    return f"users/{user_id}/resumes/v{version}/{name}"


class LocalFileStore:
    def __init__(self, root: Path):
        self.root = Path(root)

    def _path(self, key: str) -> Path:
        path = (self.root / key).resolve()
        if not path.is_relative_to(self.root.resolve()):
            raise ValueError(f"storage key escapes the store: {key!r}")
        return path

    def put(self, key: str, data: bytes) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def get(self, key: str) -> bytes:
        return self._path(key).read_bytes()
