from __future__ import annotations

import datetime

from audit_service.chain import GENESIS_HASH, canonicalize, compute_hash, verify_link

OCCURRED_AT = datetime.datetime(2026, 8, 29, 12, 0, 0, tzinfo=datetime.UTC)


def _event(**overrides):
    defaults = {
        "event_id": "evt-1",
        "type": "widget.created",
        "source": "example-service",
        "occurred_at": OCCURRED_AT,
        "trace_id": "trace-abc",
        "data": {"id": 1, "name": "gizmo"},
    }
    return {**defaults, **overrides}


def test_canonicalize_is_deterministic_regardless_of_dict_order():
    a = canonicalize(**_event(data={"id": 1, "name": "gizmo"}))
    b = canonicalize(**_event(data={"name": "gizmo", "id": 1}))
    assert a == b


def test_canonicalize_differs_for_different_content():
    a = canonicalize(**_event())
    b = canonicalize(**_event(event_id="evt-2"))
    assert a != b


def test_canonicalize_is_the_expected_byte_sequence():
    # A fixed, known vector, not just internal consistency - if the canonicalization scheme ever
    # changes, this is what should notice.
    canonical = canonicalize(**_event())
    assert canonical == (
        b'{"data":{"id":1,"name":"gizmo"},"event_id":"evt-1",'
        b'"occurred_at":"2026-08-29T12:00:00+00:00","source":"example-service",'
        b'"trace_id":"trace-abc","type":"widget.created"}'
    )


def test_compute_hash_is_a_known_vector():
    # Fixed input -> fixed output, so a change in the algorithm is caught by this test breaking,
    # not silently.
    canonical = canonicalize(**_event())
    assert (
        compute_hash(GENESIS_HASH, canonical)
        == "cfb5b68f466cb1f0dcddd20e19656cbbb6a1d302b2eab54632700bd6106ddbd4"
    )


def test_verify_link_accepts_a_correctly_computed_hash():
    canonical = canonicalize(**_event())
    h = compute_hash(GENESIS_HASH, canonical)
    assert verify_link(GENESIS_HASH, canonical, h) is True


def test_verify_link_rejects_a_tampered_event():
    canonical = canonicalize(**_event())
    h = compute_hash(GENESIS_HASH, canonical)
    tampered_canonical = canonicalize(**_event(data={"id": 1, "name": "TAMPERED"}))
    assert verify_link(GENESIS_HASH, tampered_canonical, h) is False


def test_verify_link_rejects_a_wrong_prev_hash():
    canonical = canonicalize(**_event())
    h = compute_hash(GENESIS_HASH, canonical)
    assert verify_link("f" * 64, canonical, h) is False


def test_chain_of_three_events_each_link_depends_on_the_last():
    e1, e2, e3 = _event(event_id="1"), _event(event_id="2"), _event(event_id="3")
    h0 = GENESIS_HASH
    h1 = compute_hash(h0, canonicalize(**e1))
    h2 = compute_hash(h1, canonicalize(**e2))
    h3 = compute_hash(h2, canonicalize(**e3))

    # Every link verifies against the real chain.
    assert verify_link(h0, canonicalize(**e1), h1)
    assert verify_link(h1, canonicalize(**e2), h2)
    assert verify_link(h2, canonicalize(**e3), h3)

    # Tampering event 2's content breaks link 2 *and* invalidates h2 as the prev_hash for link 3,
    # even though h3 itself was computed correctly at the time - the whole point of the chain.
    tampered_h2 = compute_hash(h1, canonicalize(**_event(event_id="2", data={"forged": True})))
    assert tampered_h2 != h2
    assert not verify_link(tampered_h2, canonicalize(**e3), h3)
