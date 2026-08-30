"""Skip-if-down: the actual end-to-end flow this harness exists to prove.

A real bearer token from a running identity-service publishes a real zip bundle to a running
skill-registry, fetched back byte-for-byte (both the parsed metadata and the raw archive
download). Steps 1-4's tests already proved validation, storage, and reads against test
databases/buckets in isolation; this is the one that proves the whole live system.
"""

from __future__ import annotations

import uuid
import zipfile
from io import BytesIO

import httpx


def _skill_zip(name: str) -> bytes:
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr(
            f"{name}/SKILL.md",
            f"---\nname: {name}\ndescription: A live-stack test skill.\n---\n\nBody.",
        )
        archive.writestr(f"{name}/scripts/run.py", "print('hi')")
    return buf.getvalue()


async def _publish_token(identity_url: str) -> str:
    async with httpx.AsyncClient(base_url=identity_url) as client:
        resp = await client.post(
            "/oauth/token",
            data={
                "grant_type": "client_credentials",
                "client_id": "example-service",
                "client_secret": "example-service-dev-secret",
                "scope": "skill_registry:publish",
            },
        )
        resp.raise_for_status()
        return resp.json()["access_token"]


async def test_a_real_skill_bundle_is_published_fetched_and_downloaded_intact(
    platform_identity_url, platform_skill_registry_url
):
    token = await _publish_token(platform_identity_url)
    headers = {"Authorization": f"Bearer {token}"}
    name = f"live-test-skill-{uuid.uuid4().hex[:8]}"
    data = _skill_zip(name)

    async with httpx.AsyncClient(base_url=platform_skill_registry_url) as client:
        published = await client.post(
            "/skills",
            files={"file": (f"{name}.zip", data, "application/zip")},
            data={"version": "1.0.0"},
            headers=headers,
        )
        assert published.status_code == 201, published.text

        fetched = await client.get(f"/skills/{name}/1.0.0")
        assert fetched.status_code == 200
        assert f"name: {name}" in fetched.json()["skill_md"]

        downloaded = await client.get(f"/skills/{name}/1.0.0/bundle")
        assert downloaded.status_code == 200
        assert downloaded.content == data
