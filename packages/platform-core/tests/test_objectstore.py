"""Skip-if-down: real MinIO (S3-compatible), matching test_db.py's posture for Postgres.

Uses a unique bucket per test run so this never touches the live `langfuse` bucket the shared
minio container already serves.
"""

from __future__ import annotations

import uuid

import pytest

from platform_core.objectstore import ObjectNotFoundError, ObjectStore


@pytest.fixture
def store(minio_endpoint) -> ObjectStore:
    bucket = f"test-{uuid.uuid4().hex[:12]}"
    return ObjectStore(minio_endpoint, "minio", "miniosecret123", bucket)


async def test_ensure_bucket_is_idempotent(store: ObjectStore):
    await store.ensure_bucket()
    await store.ensure_bucket()  # must not raise on a bucket that already exists


async def test_put_and_get_object_roundtrip(store: ObjectStore):
    await store.ensure_bucket()
    await store.put_object("skill-name/1.0.0.zip", b"hello world", content_type="application/zip")
    data = await store.get_object("skill-name/1.0.0.zip")
    assert data == b"hello world"


async def test_get_missing_object_raises_object_not_found(store: ObjectStore):
    await store.ensure_bucket()
    with pytest.raises(ObjectNotFoundError):
        await store.get_object("does/not/exist.zip")
