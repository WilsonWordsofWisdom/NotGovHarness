"""Skip-if-down: the actual end-to-end flow this harness exists to prove — a real bearer token,
a real mem0-service, a real Mem0 extraction through llm-gateway, real pgvector storage, and real
semantic search (different wording than the stored fact still retrieves it).
"""

from __future__ import annotations

import httpx


async def _token(identity_url: str, scope: str) -> str:
    async with httpx.AsyncClient(base_url=identity_url) as client:
        resp = await client.post(
            "/oauth/token",
            data={
                "grant_type": "client_credentials",
                "client_id": "example-service",
                "client_secret": "example-service-dev-secret",
                "scope": scope,
            },
        )
        resp.raise_for_status()
        return resp.json()["access_token"]


async def test_add_and_semantic_search_and_delete(platform_identity_url, platform_mem0_url):
    write_token = await _token(platform_identity_url, "memory:write")
    write_headers = {"Authorization": f"Bearer {write_token}"}

    async with httpx.AsyncClient(base_url=platform_mem0_url, timeout=30.0) as client:
        added = await client.post(
            "/memories",
            json={
                "user_id": "test-user-live-stack",
                "messages": [
                    {"role": "user", "content": "I only drink decaf coffee, never regular."}
                ],
            },
            headers=write_headers,
        )
        assert added.status_code == 201, added.text
        results = added.json()["results"]
        assert len(results) >= 1
        # A genuinely extracted fact, not the raw input echoed back verbatim.
        assert results[0]["memory"] != "I only drink decaf coffee, never regular."
        memory_id = results[0]["id"]

        # Different wording than the stored fact — proves real semantic search, not keyword match.
        searched = await client.get(
            "/memories",
            params={"user_id": "test-user-live-stack", "query": "caffeine preferences"},
            headers=write_headers,
        )
        assert searched.status_code == 200
        found_ids = {r["id"] for r in searched.json()["results"]}
        assert memory_id in found_ids

        deleted = await client.delete(f"/memories/{memory_id}", headers=write_headers)
        assert deleted.status_code == 204


async def test_read_without_write_scope_still_works_but_write_is_rejected(
    platform_identity_url, platform_mem0_url
):
    read_only_token = await _token(platform_identity_url, "upstream:call")

    async with httpx.AsyncClient(base_url=platform_mem0_url, timeout=15.0) as client:
        listed = await client.get(
            "/memories",
            params={"user_id": "anyone"},
            headers={"Authorization": f"Bearer {read_only_token}"},
        )
        assert listed.status_code == 200

        forbidden = await client.post(
            "/memories",
            json={"user_id": "anyone", "messages": [{"role": "user", "content": "test"}]},
            headers={"Authorization": f"Bearer {read_only_token}"},
        )
        assert forbidden.status_code == 403
