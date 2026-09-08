"""Skip-if-down: the actual end-to-end flow this harness exists to prove — real ingest, real
hybrid search + rerank + generation through llm-gateway, real provenance, and real visibility
enforcement (D-068) between two distinct callers.
"""

from __future__ import annotations

import uuid

import httpx


async def _token(identity_url: str, client_id: str, client_secret: str, scope: str) -> str:
    async with httpx.AsyncClient(base_url=identity_url) as client:
        resp = await client.post(
            "/oauth/token",
            data={
                "grant_type": "client_credentials",
                "client_id": client_id,
                "client_secret": client_secret,
                "scope": scope,
            },
        )
        resp.raise_for_status()
        return resp.json()["access_token"]


async def test_ingest_and_query_with_provenance(platform_identity_url, platform_knowledge_url):
    token = await _token(
        platform_identity_url, "example-service", "example-service-dev-secret", "knowledge:write"
    )
    headers = {"Authorization": f"Bearer {token}"}
    # Unique-ish fact so this test doesn't collide with other runs' ingested data.
    marker = uuid.uuid4().hex[:8]

    async with httpx.AsyncClient(base_url=platform_knowledge_url, timeout=30.0) as client:
        ingested = await client.post(
            "/documents",
            json={
                "title": f"Policy {marker}",
                "text": (
                    f"The parking reimbursement limit for badge {marker} is 50 dollars per month."
                ),
                "visibility": "shared",
            },
            headers=headers,
        )
        assert ingested.status_code == 201, ingested.text
        document_id = ingested.json()["id"]
        assert ingested.json()["chunk_count"] >= 1

        # Different wording than the document's own text.
        answered = await client.post(
            "/query",
            json={"question": f"what is the monthly parking reimbursement cap for {marker}"},
            headers=headers,
        )
        assert answered.status_code == 200, answered.text
        body = answered.json()
        assert "50" in body["answer"]
        assert document_id in body["source_document_ids"]


async def test_private_documents_are_not_visible_to_other_callers(
    platform_identity_url, platform_knowledge_url
):
    bob_token = await _token(platform_identity_url, "bob", "bob-dev-secret", "knowledge:write")
    example_token = await _token(
        platform_identity_url, "example-service", "example-service-dev-secret", "knowledge:write"
    )
    marker = uuid.uuid4().hex[:8]

    async with httpx.AsyncClient(base_url=platform_knowledge_url, timeout=30.0) as client:
        ingested = await client.post(
            "/documents",
            json={
                "title": "Bob's private note",
                "text": f"The private access code for room {marker} is 7-7-2-9.",
                "visibility": "private",
            },
            headers={"Authorization": f"Bearer {bob_token}"},
        )
        assert ingested.status_code == 201, ingested.text
        private_doc_id = ingested.json()["id"]

        # A different caller must not see it, in the query results or the answer.
        as_other = await client.post(
            "/query",
            json={"question": f"what is the access code for room {marker}"},
            headers={"Authorization": f"Bearer {example_token}"},
        )
        assert as_other.status_code == 200
        assert private_doc_id not in as_other.json()["source_document_ids"]

        other_listing = await client.get(
            "/documents", headers={"Authorization": f"Bearer {example_token}"}
        )
        assert private_doc_id not in {d["id"] for d in other_listing.json()}

        # The owning caller must still see it.
        as_owner = await client.post(
            "/query",
            json={"question": f"what is the access code for room {marker}"},
            headers={"Authorization": f"Bearer {bob_token}"},
        )
        assert as_owner.status_code == 200
        assert private_doc_id in as_owner.json()["source_document_ids"]
        assert "7-7-2-9" in as_owner.json()["answer"]


async def test_write_requires_scope_but_query_is_open_to_any_caller(
    platform_identity_url, platform_knowledge_url
):
    read_only_token = await _token(
        platform_identity_url, "example-service", "example-service-dev-secret", "upstream:call"
    )

    async with httpx.AsyncClient(base_url=platform_knowledge_url, timeout=15.0) as client:
        forbidden = await client.post(
            "/documents",
            json={"title": "x", "text": "y"},
            headers={"Authorization": f"Bearer {read_only_token}"},
        )
        assert forbidden.status_code == 403

        allowed = await client.post(
            "/query",
            json={"question": "anything"},
            headers={"Authorization": f"Bearer {read_only_token}"},
        )
        assert allowed.status_code == 200
