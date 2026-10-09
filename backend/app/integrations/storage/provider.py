from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import BinaryIO, Protocol

from app.core.config import Settings


class StorageProvider(Protocol):
    def put(self, key: str, content: bytes, content_type: str) -> None: ...
    def open(self, key: str) -> BinaryIO: ...
    def delete(self, key: str) -> None: ...


class LocalStorage:
    def __init__(self, root: str) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        candidate = (self.root / key).resolve()
        if self.root != candidate and self.root not in candidate.parents:
            raise ValueError("invalid storage key")
        return candidate

    def put(self, key: str, content: bytes, content_type: str) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    def open(self, key: str) -> BinaryIO:
        return self._path(key).open("rb")

    def delete(self, key: str) -> None:
        path = self._path(key)
        if path.exists():
            path.unlink()


class S3Storage:
    def __init__(self, settings: Settings) -> None:
        import boto3

        self.bucket = settings.s3_bucket
        self.client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url,
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key,
            region_name=settings.s3_region,
        )

    def put(self, key: str, content: bytes, content_type: str) -> None:
        self.client.put_object(Bucket=self.bucket, Key=key, Body=content, ContentType=content_type)

    def open(self, key: str) -> BinaryIO:
        return self.client.get_object(Bucket=self.bucket, Key=key)["Body"]

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=key)


@lru_cache(maxsize=8)
def _cached_storage(backend: str, local_path: str, endpoint: str | None, bucket: str, access: str | None, secret: str | None, region: str):
    if backend == "local":
        return LocalStorage(local_path)
    settings = Settings(
        storage_backend="s3",
        s3_endpoint_url=endpoint,
        s3_bucket=bucket,
        s3_access_key=access,
        s3_secret_key=secret,
        s3_region=region,
    )
    return S3Storage(settings)


def get_storage(settings: Settings) -> StorageProvider:
    return _cached_storage(
        settings.storage_backend,
        settings.storage_local_path,
        settings.s3_endpoint_url,
        settings.s3_bucket,
        settings.s3_access_key,
        settings.s3_secret_key,
        settings.s3_region,
    )
