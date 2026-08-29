"""Walks the chain, recomputing every row's hash from its stored content.

Takes plain `AuditLog` instances rather than a DB session — SQLAlchemy declarative models can be
built in-memory with no connection, so this is testable against a seeded stub chain with no
Postgres involved, per the harness spec's step-4 verify criterion.
"""

from __future__ import annotations

from dataclasses import dataclass

from .chain import GENESIS_HASH, canonicalize, verify_link
from .models import AuditLog


@dataclass
class VerifyResult:
    valid: bool
    checked: int
    broken_at: int | None


def verify_chain(rows: list[AuditLog]) -> VerifyResult:
    """Recompute each row's hash from scratch, checking it against both the row's own stored
    hash *and* that it correctly follows the previous row — either mismatch is tampering.
    """
    prev_hash = GENESIS_HASH
    for checked, row in enumerate(rows, start=1):
        canonical = canonicalize(
            event_id=row.event_id,
            type=row.type,
            source=row.source,
            occurred_at=row.occurred_at,
            trace_id=row.trace_id,
            data=row.data,
        )
        if row.prev_hash != prev_hash or not verify_link(row.prev_hash, canonical, row.hash):
            return VerifyResult(valid=False, checked=checked, broken_at=row.id)
        prev_hash = row.hash
    return VerifyResult(valid=True, checked=len(rows), broken_at=None)
