"""Where uploaded resume files live, and how browsers reach them.

Decided 2026-09-16: one private bucket with per-user keys
(users/<id>/resumes/v<n>/<file>), files encrypted at rest with KMS, and no
browser ever holding storage credentials. The API hands out:
- an upload ticket: a presigned POST valid for one exact key, 1 byte to 5 MB,
  KMS encryption required, for 5 minutes;
- a download link: a presigned GET for one key, sent as an attachment, for
  5 minutes.
Links are signed on demand and never stored; the database keeps only the key.

S3ResumeStorage is production. DevSignedStorage gives local development the
same signed, expiring links, served by cloud_api/dev_storage.py.
"""

import hashlib
import hmac
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlencode

from resume.store import LocalFileStore

MAX_RESUME_BYTES = 5 * 1024 * 1024
URL_EXPIRES_SECONDS = 300


@dataclass
class UploadTicket:
    url: str                       # where the browser POSTs the multipart form
    fields: dict                   # form fields to send before the file
    expires_in: int = URL_EXPIRES_SECONDS
    policy_conditions: list = field(default_factory=list)


def _download_name(filename: str) -> str:
    return re.sub(r"[^\w.-]", "_", Path(filename).name) or "resume"


def make_s3_client(region: str):
    """An S3 client that signs with Signature V4, which KMS-encrypted objects and
    presigned links with X-Amz-Expires require."""
    import boto3
    from botocore.config import Config

    return boto3.client("s3", region_name=region, config=Config(signature_version="s3v4"))


class S3ResumeStorage:
    def __init__(self, client, bucket: str, kms_key_id: str | None = None):
        self.client = client
        self.bucket = bucket
        self.kms_key_id = kms_key_id

    def presign_upload(self, key: str) -> UploadTicket:
        fields = {"x-amz-server-side-encryption": "aws:kms"}
        if self.kms_key_id:
            fields["x-amz-server-side-encryption-aws-kms-key-id"] = self.kms_key_id
        conditions = [{"key": key}, ["content-length-range", 1, MAX_RESUME_BYTES], *({k: v} for k, v in fields.items())]
        post = self.client.generate_presigned_post(Bucket=self.bucket, Key=key, Fields=fields,
                                                   Conditions=conditions, ExpiresIn=URL_EXPIRES_SECONDS)
        return UploadTicket(url=post["url"], fields=post["fields"], policy_conditions=conditions)

    def presign_download(self, key: str, filename: str) -> str:
        return self.client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": key,
                    "ResponseContentDisposition": f'attachment; filename="{_download_name(filename)}"'},
            ExpiresIn=URL_EXPIRES_SECONDS,
        )

    def read(self, key: str) -> bytes | None:
        try:
            return self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read()
        except self.client.exceptions.NoSuchKey:
            return None

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=key)

    def delete_prefix(self, prefix: str) -> None:
        for page in self.client.get_paginator("list_objects_v2").paginate(Bucket=self.bucket, Prefix=prefix):
            objects = [{"Key": o["Key"]} for o in page.get("Contents", [])]
            if objects:
                self.client.delete_objects(Bucket=self.bucket, Delete={"Objects": objects})


def sign(secret: bytes, action: str, key: str, expires: int) -> str:
    return hmac.new(secret, f"{action}\n{key}\n{expires}".encode(), hashlib.sha256).hexdigest()


class DevSignedStorage:
    """Local development: files on disk, links signed with HMAC and expiring
    like S3's. Only cloud_api's dev-mode routes accept them."""

    def __init__(self, root: Path, secret: bytes, base_url: str):
        self.files = LocalFileStore(root)
        self.secret = secret
        self.base_url = base_url.rstrip("/")

    def _signed(self, action: str, key: str) -> dict:
        expires = int(time.time()) + URL_EXPIRES_SECONDS
        return {"key": key, "expires": str(expires), "signature": sign(self.secret, action, key, expires)}

    def verify(self, action: str, key: str, expires: int, signature: str) -> bool:
        if expires < time.time():
            return False
        return hmac.compare_digest(sign(self.secret, action, key, expires), signature or "")

    def presign_upload(self, key: str) -> UploadTicket:
        return UploadTicket(url=f"{self.base_url}/dev-storage/upload", fields=self._signed("upload", key))

    def presign_download(self, key: str, filename: str) -> str:
        query = {**self._signed("download", key), "filename": _download_name(filename)}
        return f"{self.base_url}/dev-storage/file?{urlencode(query)}"

    def write(self, key: str, data: bytes) -> None:
        if not 1 <= len(data) <= MAX_RESUME_BYTES:
            raise ValueError(f"file must be 1 byte to {MAX_RESUME_BYTES} bytes")
        self.files.put(key, data)

    def read(self, key: str) -> bytes | None:
        try:
            return self.files.get(key)
        except FileNotFoundError:
            return None

    def delete(self, key: str) -> None:
        path = self.files._path(key)
        if path.exists():
            path.unlink()

    def delete_prefix(self, prefix: str) -> None:
        root = self.files._path(prefix)
        for path in sorted(root.rglob("*"), reverse=True) if root.exists() else []:
            path.unlink() if path.is_file() else path.rmdir()
        if root.is_dir():
            root.rmdir()
