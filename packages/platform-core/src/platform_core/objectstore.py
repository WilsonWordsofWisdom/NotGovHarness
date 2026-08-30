"""Minimal MinIO (S3-compatible object storage) wrapper, mirroring ``platform_core.db.Database``'s
shape (construct, lifespan-manage, hand out to routes) — built now because Skill Registry and the
next Wave 2 harness (Eval Registry) both need MinIO (see the Skill Registry harness design, D-034).

The official ``minio`` SDK is synchronous; every call here runs in a worker thread
(``starlette.concurrency.run_in_threadpool``) so it never blocks the event loop. Unlike D-032's
self-referential deadlock (a service blocking on a call to *itself*), a call to a genuinely
separate MinIO container can't deadlock this way — but blocking the loop for I/O under concurrent
load is still worth avoiding.
"""

from __future__ import annotations

import io

from minio import Minio
from minio.error import S3Error
from starlette.concurrency import run_in_threadpool


class ObjectNotFoundError(Exception):
    def __init__(self, key: str) -> None:
        self.key = key
        super().__init__(f"object not found: {key!r}")


class ObjectStore:
    def __init__(
        self,
        endpoint: str,
        access_key: str,
        secret_key: str,
        bucket: str,
        *,
        secure: bool = False,
    ) -> None:
        self._client = Minio(endpoint, access_key=access_key, secret_key=secret_key, secure=secure)
        self._bucket = bucket

    async def ensure_bucket(self) -> None:
        def _ensure() -> None:
            if not self._client.bucket_exists(self._bucket):
                self._client.make_bucket(self._bucket)

        await run_in_threadpool(_ensure)

    async def put_object(
        self, key: str, data: bytes, *, content_type: str = "application/octet-stream"
    ) -> None:
        def _put() -> None:
            self._client.put_object(
                self._bucket, key, io.BytesIO(data), length=len(data), content_type=content_type
            )

        await run_in_threadpool(_put)

    async def get_object(self, key: str) -> bytes:
        def _get() -> bytes:
            resp = self._client.get_object(self._bucket, key)
            try:
                return resp.read()
            finally:
                resp.close()
                resp.release_conn()

        try:
            return await run_in_threadpool(_get)
        except S3Error as exc:
            if exc.code == "NoSuchKey":
                raise ObjectNotFoundError(key) from exc
            raise
