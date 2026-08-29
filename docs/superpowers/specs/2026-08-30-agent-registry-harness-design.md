# Agent Registry Harness — Design

**Status:** built and verified — all 6 done-when criteria confirmed live: a real signed Agent
Card, signed by a running identity-service and published to a running agent-registry, was fetched
back byte-for-byte, and a card tampered with directly in Postgres (bypassing every service) was
caught by `/verify` at exactly that row. Not yet merged to `main`.
**Date:** 2026-08-30
**Wave:** 2 (Catalog & Registries) · first of three
**Branch:** `feat/wave2-agent-registry`

Related: [../../architecture.md](../../architecture.md) ·
[../../decisions.md](../../decisions.md) (D-010) ·
[../../implementation-plan.md](../../implementation-plan.md)

## Context

D-010: "A2A Agent Cards are signed and agents are non-human identities calling other
agents/tools; SPIFFE is the 2026 de-facto workload-identity standard." The decisions table names
the standard explicitly: **A2A Agent Cards (signed), Linux Foundation — build to standard.**
Confirmed against the current spec (A2A v1.0.1, May 2026): `AgentCard` is the canonical
self-description a service publishes (name, url, version, capabilities, skills, security
schemes), and **Signed Agent Cards using JSON Web Signatures** are part of the v1.0 enterprise
security feature set — cryptographic tamper-evidence layered on top of whatever transport auth is
used.

**The design tension:** `architecture.md`'s sequence diagram shows Agent Builder (Wave 4, not
built yet) as the publisher of signed cards (`AB -->|publish signed Agent Card, REST| REG`). This
harness can't wait for Wave 4. Resolution, matching the same "simulated principal" pattern the
Identity harness used for its delegation demo (standing in "alice" for a real human principal):
any existing OAuth2 client (`example-service`) stands in for a future Agent Builder as a card
publisher. The registry's contract doesn't change when a real Agent Builder shows up later — it
still just receives a signed card over REST.

**Who signs:** `implementation-plan.md` already calls Identity "the trust root that signs Agent
Cards" (Wave 1 entry, written before this harness existed). This harness makes that literal:
identity-service — the OAuth2 authorization server built in Wave 1, which already holds an RSA
signing key and publishes its own JWKS — gains one new endpoint, `POST /cards/sign`, so it signs
Agent Cards with the *same* key it already uses to sign OAuth2 tokens. No second key-management
story, no new trust root; the registry verifies a card's signature against identity-service's
existing `/.well-known/jwks.json`, exactly the way any resource server already verifies a bearer
token.

## Goal & success criteria

Publish, store, and cryptographically verify A2A Agent Cards, proving the same tamper-evidence
property Audit proved for events — this time for the catalog entry that will eventually let one
agent discover and call another.

**Done when:**
1. `identity-service` exposes `POST /cards/sign` (scope-gated `agentcard:sign`): given a card
   payload, returns a JWS (RS256) over it, signed with identity-service's existing signing key.
2. `agent-registry` exposes `POST /agents` (scope-gated `registry:publish`): given a card +
   signature, verifies the JWS against identity-service's JWKS *and* that the signed payload
   matches the submitted card content, then stores it. Invalid/mismatched signatures are rejected.
3. `GET /agents`, `GET /agents/{name}`, `GET /agents/{name}/{version}` — read the catalog.
4. `GET /agents/{name}/{version}/verify` — re-verifies the stored signature on demand against
   identity-service's JWKS.
5. A test that tampers with a stored card's content directly in Postgres (bypassing every service,
   same discipline as Audit's live tampering test) and confirms `/verify` catches it.
6. Unit tests (infra-free: signing/verification round-trip against a stub JWKS) + integration
   tests (skip-if-down: real identity-service + Postgres) green; `task lint` clean.

## Non-goals (YAGNI)

Full RFC 8785 JSON Canonicalization Scheme — PyJWT's own JSON encoding of the payload dict is the
signed content (see Risks); a byte-exact JCS canonicalizer is a real spec detail this reference
doesn't need to demonstrate the actual property (cryptographic tamper-evidence on a catalog
entry). A2A's `interfaces`/`extensions`/`securitySchemes` fields are stored verbatim as opaque
JSON (schema-validated for shape, not interpreted) — no harness yet calls an agent found in this
registry, so there's nothing to consume those fields. Agent-to-agent discovery/routing (that's
Agent Gateway, Wave 3). Card revocation/deactivation. A UI. Kafka eventing on publish — no
consumer exists yet for a `agent_registry.published` topic; adding one later is additive, not a
redesign (same reasoning as Audit's single-topic scoping).

## Components

- **`identity-service`** (existing, Wave 1) — gains one route, `POST /cards/sign`, reusing its
  existing `SigningKey`. No new key, no new service.
- **`agent-registry`** (new) — greenfield FastAPI (façade-free, like `identity-service` and
  `audit-service`). Postgres `agent_registry` DB (db-per-service). No Kafka consumer — this is a
  catalog CRUD service, not an event listener.

## Card signing

Payload = the `AgentCard` fields below, as a plain JSON object (not yet JWS-wrapped):

```
{name, description?, url, version, provider?, capabilities, defaultInputModes?,
 defaultOutputModes?, skills?, securitySchemes?, security?, interfaces?, extensions?}
```

`POST /cards/sign` on identity-service: `jwt.encode(card, signing_key.private_pem,
algorithm="RS256", headers={"kid": signing_key.kid})` → returns `{signing_algorithm: "RS256",
signing_key_id: <kid>, signature_value: <JWS compact>}`. Requires a bearer token carrying
`agentcard:sign` (verified via identity-service's own JWKS — self-referential, same as any other
resource server; identity-service already issues and can verify its own tokens).

`POST /agents` on agent-registry verifies: fetch identity-service's JWKS (`jwt.PyJWKClient`, same
tool `platform_core.auth` already uses for bearer-token verification), get the signing key for
`signature_value`'s `kid`, `jwt.decode(signature_value, key, algorithms=[signing_algorithm])` →
the decoded payload must equal the submitted `card` object exactly. Any mismatch (wrong key,
tampered card, wrong algorithm) is a 401.

## API

- `POST /cards/sign` (identity-service, scope `agentcard:sign`) — sign a card payload.
- `POST /agents` (agent-registry, scope `registry:publish`) — publish `{card, signature}`;
  verifies, upserts on `(name, version)`.
- `GET /agents?skill=` — list (summary: name, version, description, url, skill names).
- `GET /agents/{name}` — latest version's full card.
- `GET /agents/{name}/{version}` — one version's full card.
- `GET /agents/{name}/{version}/verify` — `{"valid": bool, "reason": str | null}`.

## Storage

- **`agent_registry` Postgres DB** — one `agent_cards` table: `id` (serial PK), `name`, `version`
  (unique together), `url`, `description`, `provider`/`capabilities`/`default_input_modes`/
  `default_output_modes`/`skills`/`security_schemes`/`security`/`interfaces`/`extensions` (jsonb,
  mirroring `AgentCard` fields), `card` (jsonb — the exact signed payload, what verification
  re-checks against), `signing_algorithm`, `signing_key_id`, `signature_value`, `published_by`
  (the publishing caller's identity), `created_at`. Alembic migration, existing per-service
  pattern.

## Build order (dependency-ordered)

1. **Card signing on identity-service** — `POST /cards/sign` + `agentcard:sign` scope seeded on
   the `example-service` demo client (extends the existing seed migration pattern). *Verify
   (unit, infra-free):* sign then verify round-trip against the app's own JWKS endpoint.
2. **`agent-registry` scaffold + migration** — copier-scaffolded service, `agent_cards` table.
   *Verify:* migration applies cleanly.
3. **Publish endpoint** — `POST /agents`, JWKS-backed signature verification, upsert. *Verify
   (unit):* a stub JWKS server (mirroring upstream-stub's `test_echo.py` pattern) — valid
   signature accepted, tampered/wrong-key signature rejected.
4. **Read endpoints** — list/get/verify. *Verify (unit):* against seeded rows.
5. **Compose integration + live tampering test** — wire `agent-registry` into `docker-compose.yml`
   (new `registry` profile), sign a real card via a running identity-service, publish it, fetch it
   back, then corrupt the stored `card` directly in Postgres and confirm `/verify` reports
   `valid: false`. *Verify:* skip-if-down, matching Audit's pattern.

## Testing strategy

- **Unit (infra-free):** sign/verify round-trip against a stub JWKS (no real identity-service
  needed, same technique `upstream-stub`'s tests already use).
- **Integration (skip-if-down):** full publish → fetch → tamper → re-verify loop against real
  identity-service + Postgres, per step 5.

## Risks / watch-items

- **Not byte-exact RFC 8785 JCS canonicalization.** The spec says "the entire canonical AgentCard
  object" is signed; this harness signs whatever `jwt.encode` produces from the payload dict and
  verifies by comparing *decoded* payload equality, not re-serialized byte equality — semantically
  equivalent tamper-evidence, but not interoperable with a strict external A2A verifier expecting
  JCS bytes. Acceptable for a reference platform proving the mechanism; would need a real JCS
  implementation before this card format is presented to a third-party A2A consumer.
- **Self-referential JWKS-over-HTTP deadlocks a single-worker server — found live, fixed with
  `verify_own_token`.** The original design called for protecting `/cards/sign` with
  `platform_core.auth`'s standard `oauth2`/`hybrid` mode, pointed at identity-service's own JWKS
  endpoint. The very first real request hung until timeout: the synchronous JWKS-over-HTTP fetch
  inside the request handler blocks the one event-loop thread uvicorn (single worker) needs free
  to accept and answer that same self-directed connection. Fixed by authenticating via
  `verify_own_token` (already used for token-exchange) instead — no network hop, since
  identity-service already holds `signing_key` in-process. See D-032. Every downstream JWKS
  consumer (`agent-registry` verifying card signatures, `upstream-stub` verifying bearer tokens)
  is unaffected — they verify a *different* service's material, not their own.
