"""Hash-chain math: canonicalize an event, compute a row's hash, verify one link.

Same idea as a git commit chain — each row's hash covers the previous row's hash plus its own
content, so any historical edit breaks every hash from that point forward. Pure functions, no I/O:
the actual chaining (reading the previous row, writing the next) lives in the DB layer that calls
these; this module is what makes that logic testable without Postgres.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

GENESIS_HASH = "0" * 64


def canonicalize(
    *,
    event_id: str,
    type: str,  # noqa: A002 - matches EventEnvelope.type; not shadowing anything here
    source: str,
    occurred_at: datetime,
    trace_id: str | None,
    data: dict[str, Any],
) -> bytes:
    """A deterministic byte representation of an event's audit-relevant fields.

    Sorted keys + fixed separators so the same event always canonicalizes identically regardless
    of dict insertion order — required for re-verification to recompute the same hash later.
    """
    payload = {
        "event_id": event_id,
        "type": type,
        "source": source,
        "occurred_at": occurred_at.isoformat(),
        "trace_id": trace_id,
        "data": data,
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()


def compute_hash(prev_hash: str, canonical_event: bytes) -> str:
    """The next row's hash: SHA-256 of the previous hash concatenated with this row's content."""
    return hashlib.sha256(prev_hash.encode() + canonical_event).hexdigest()


def verify_link(prev_hash: str, canonical_event: bytes, claimed_hash: str) -> bool:
    """Does recomputing this row's hash from scratch match what's stored?"""
    return compute_hash(prev_hash, canonical_event) == claimed_hash
