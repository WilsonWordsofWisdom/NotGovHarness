"""Infra-free: AuditLog is a plain SQLAlchemy model, buildable in-memory with no DB connection —
so a seeded stub chain is enough to test verify_chain(), per the spec's step-4 verify criterion.
"""

from __future__ import annotations

import datetime

from audit_service.chain import GENESIS_HASH, canonicalize, compute_hash
from audit_service.models import AuditLog
from audit_service.verifier import verify_chain

OCCURRED_AT = datetime.datetime(2026, 8, 29, 12, 0, 0, tzinfo=datetime.UTC)


def _row(id: int, event_id: str, prev_hash: str, **overrides) -> AuditLog:  # noqa: A002
    fields = {
        "event_id": event_id,
        "type": "widget.created",
        "source": "example-service",
        "occurred_at": OCCURRED_AT,
        "trace_id": None,
        "data": {"n": id},
        **overrides,
    }
    canonical = canonicalize(
        event_id=fields["event_id"],
        type=fields["type"],
        source=fields["source"],
        occurred_at=fields["occurred_at"],
        trace_id=fields["trace_id"],
        data=fields["data"],
    )
    return AuditLog(
        id=id,
        prev_hash=prev_hash,
        hash=compute_hash(prev_hash, canonical),
        **fields,
    )


def _valid_chain(n: int) -> list[AuditLog]:
    rows = []
    prev = GENESIS_HASH
    for i in range(1, n + 1):
        row = _row(i, f"evt-{i}", prev)
        rows.append(row)
        prev = row.hash
    return rows


def test_empty_chain_is_valid():
    result = verify_chain([])
    assert result.valid is True
    assert result.checked == 0
    assert result.broken_at is None


def test_valid_chain_of_three():
    result = verify_chain(_valid_chain(3))
    assert result.valid is True
    assert result.checked == 3
    assert result.broken_at is None


def test_first_row_with_wrong_genesis_is_broken():
    rows = _valid_chain(1)
    rows[0].prev_hash = "f" * 64  # doesn't match GENESIS_HASH
    result = verify_chain(rows)
    assert result.valid is False
    assert result.checked == 1
    assert result.broken_at == rows[0].id


def test_tampering_a_middle_row_content_is_caught_at_that_row():
    rows = _valid_chain(5)
    rows[2].data = {"tampered": True}  # row id 3, hash stored no longer matches recomputed
    result = verify_chain(rows)
    assert result.valid is False
    assert result.checked == 3
    assert result.broken_at == rows[2].id


def test_tampering_a_middle_row_also_breaks_the_next_rows_prev_hash_link():
    rows = _valid_chain(5)
    # Directly forge row 3's stored hash to match tampered content, *without* fixing row 4's
    # prev_hash - simulates an attacker rewriting one row but not the whole tail.
    tampered_canonical = canonicalize(
        event_id=rows[2].event_id,
        type=rows[2].type,
        source=rows[2].source,
        occurred_at=rows[2].occurred_at,
        trace_id=rows[2].trace_id,
        data={"tampered": True},
    )
    rows[2].data = {"tampered": True}
    rows[2].hash = compute_hash(rows[2].prev_hash, tampered_canonical)
    # Row 3 alone now verifies fine (self-consistent) - but row 4 still expects the *original*
    # row 3 hash as its prev_hash, so the break surfaces at row 4, not row 3.
    result = verify_chain(rows)
    assert result.valid is False
    assert result.broken_at == rows[3].id
