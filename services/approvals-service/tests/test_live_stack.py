"""Skip-if-down: the actual end-to-end flow this harness exists to prove — and, per D-049, the
first committed automated test exercising Temporal for real (not the workflow-only,
no-live-server tests in test_workflow.py).

A real requester token starts a real Temporal workflow through approvals-service; a real
reviewer token signals it; the workflow's own persist_outcome activity writes the terminal state
back to Postgres — proven by observing it through the API, not by asserting anything about
Temporal internals directly.
"""

from __future__ import annotations

import asyncio

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


async def test_approve_flow_goes_through_a_real_temporal_workflow(
    platform_identity_url, platform_approvals_url
):
    requester_token = await _token(
        platform_identity_url, "example-service", "example-service-dev-secret", "approvals:request"
    )
    reviewer_token = await _token(
        platform_identity_url, "reviewer", "reviewer-dev-secret", "approvals:decide"
    )

    async with httpx.AsyncClient(base_url=platform_approvals_url, timeout=20.0) as client:
        created = await client.post(
            "/approvals",
            json={
                "action_type": "delete_production_record",
                "action_payload": {"record_id": "widget-42"},
                "risk_level": "high",
            },
            headers={"Authorization": f"Bearer {requester_token}"},
        )
        assert created.status_code == 201, created.text
        approval = created.json()
        assert approval["status"] == "pending"
        assert approval["workflow_id"]

        fetched = await client.get(
            f"/approvals/{approval['id']}", headers={"Authorization": f"Bearer {requester_token}"}
        )
        assert fetched.status_code == 200
        assert fetched.json()["status"] == "pending"

        decided = await client.post(
            f"/approvals/{approval['id']}/decide",
            json={"decision": "approve"},
            headers={"Authorization": f"Bearer {reviewer_token}"},
        )
        assert decided.status_code in (200, 202), decided.text
        body = decided.json()
        assert body["status"] == "approved"
        assert body["decided_by"]

        # A second decision on the same (now-terminal) approval must be rejected — the workflow
        # only accepts its first signal, and the API layer should refuse to even try a second.
        redecided = await client.post(
            f"/approvals/{approval['id']}/decide",
            json={"decision": "reject"},
            headers={"Authorization": f"Bearer {reviewer_token}"},
        )
        assert redecided.status_code == 409


async def test_requester_cannot_decide_their_own_request(
    platform_identity_url, platform_approvals_url
):
    requester_token = await _token(
        platform_identity_url, "example-service", "example-service-dev-secret", "approvals:request"
    )

    async with httpx.AsyncClient(base_url=platform_approvals_url, timeout=20.0) as client:
        created = await client.post(
            "/approvals",
            json={
                "action_type": "delete_production_record",
                "action_payload": {"record_id": "widget-43"},
                "risk_level": "high",
            },
            headers={"Authorization": f"Bearer {requester_token}"},
        )
        assert created.status_code == 201, created.text
        approval_id = created.json()["id"]

        # example-service's own token carries approvals:request, not approvals:decide — the
        # scope separation from the design (requester != reviewer) is enforced here, live.
        forbidden = await client.post(
            f"/approvals/{approval_id}/decide",
            json={"decision": "approve"},
            headers={"Authorization": f"Bearer {requester_token}"},
        )
        assert forbidden.status_code == 403


async def test_an_undecided_approval_expires_on_its_own_durable_timeout(
    platform_identity_url, platform_approvals_url
):
    requester_token = await _token(
        platform_identity_url, "example-service", "example-service-dev-secret", "approvals:request"
    )

    async with httpx.AsyncClient(base_url=platform_approvals_url, timeout=20.0) as client:
        created = await client.post(
            "/approvals",
            json={
                "action_type": "delete_production_record",
                "action_payload": {"record_id": "widget-44"},
                "risk_level": "low",
                # A near-zero timeout so this test doesn't wait a real 24h — the workflow's own
                # timer resolves it, no cron or poll loop in approvals-service does this.
                "timeout_hours": 1 / 3600,
            },
            headers={"Authorization": f"Bearer {requester_token}"},
        )
        assert created.status_code == 201, created.text
        approval_id = created.json()["id"]

        # Nobody ever decides — poll until the workflow's own timeout lands in Postgres.
        for _ in range(20):
            fetched = await client.get(
                f"/approvals/{approval_id}", headers={"Authorization": f"Bearer {requester_token}"}
            )
            if fetched.json()["status"] == "expired":
                break
            await asyncio.sleep(1)
        else:
            raise AssertionError("approval never reached 'expired' within 20s")
