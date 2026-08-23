# Agent Identity Harness — Design

**Status:** approved design, pre-implementation
**Date:** 2026-08-23
**Wave:** 1 (Foundation) · first harness
**Branch:** `feat/wave1-agent-identity`

Related: [../../architecture.md](../../architecture.md) · [../../decisions.md](../../decisions.md)
(D-010) · [../../implementation-plan.md](../../implementation-plan.md)

## Context

Agent Identity is the seam every other harness authenticates through — it replaces the
Phase 0 `platform-core.auth` dev stub. Research (CNCF 2026, IETF WIMSE, RFC 8693, AIP draft-00)
confirms the foundation (SPIFFE + OAuth2 + JWKS) but shows the *defining* agent-identity capability
is **delegation** (an agent acting on-behalf-of a principal, down an accountable call chain) —
absent from plain client-credentials. The interop protocols we build on (MCP, A2A) verify neither
identity (a scan of ~2,000 MCP servers found all lacked auth; A2A cards are self-declared), so this
harness plus signed Agent Cards (Wave 2) and the Audit plane close that gap.

**Approach:** adopt the stable standards (SPIFFE/SPIRE + OAuth2 client-credentials + JWKS +
SVID-authed tokens + **RFC 8693 token exchange**), shape the token claims like **AIP/IBCT**
(invocation-bound, provenance, delegated-vs-autonomous, delegation-depth limit), and keep the
cutting edge as documented seams (Biscuit, full AIP wire format, Transaction Tokens, OPA,
cross-org federation).

## Goal & success criteria

Provide cryptographic workload identity (SVIDs) + scoped, delegable authorization tokens, and make
`platform-core` verify them — proven on the example-service → upstream-stub hop.

**Done when:**
1. `identity-service` issues an RS256 JWT for `grant_type=client_credentials` and publishes JWKS;
   a service verifies it via `platform-core` `oauth2` mode.
2. `identity-service` issues a **delegated** token via **RFC 8693 token exchange**
   (`subject_token` + `actor_token`) carrying an `act` chain, `mode=delegated`, `prov`, and `depth`.
3. `platform-core` enforces signature/iss/aud/exp/scope **and max-delegation-depth** on verify,
   yielding a `CallerIdentity` with `mode` / `on_behalf_of` / `actor_chain`.
4. SPIRE issues X.509 SVIDs to workloads; example-service → upstream-stub runs over **mTLS** with
   mutual SPIFFE-ID verification.
5. The demo hop carries a delegated token end to end; logs + one Jaeger trace show `spiffe_id`,
   `sub`, and the `act` chain.
6. Unit tests (infra-free) + integration tests (skip-if-down) green; `task lint` clean.

## Non-goals (YAGNI)

Biscuit / full AIP wire format; OAuth Transaction Tokens; OPA policy engine; cross-org federation
/ identity mesh; token refresh & revocation lists; human/OIDC login; full mTLS on every hop
(only the demo hop). Each is a documented seam, not built.

## Components

- **`spire-server`** — trust domain `notgovharness`; own datastore (SQLite in a volume); holds
  registration entries + trust bundle.
- **`spire-agent`** — node agent, **Docker workload attestor**; exposes the Workload API on a unix
  socket shared into service containers via a volume.
- **`spire-registrar`** — an init step running `spire-server entry create` to map each service's
  compose selector (`docker:label:com.docker.compose.service:<svc>`) → `spiffe://notgovharness/<svc>`.
- **`identity-service`** — greenfield FastAPI (façade-free); the OAuth2 authz server + trust anchor.
  Postgres `identity` DB.

## `identity-service` — API

- `POST /oauth/token`
  - `grant_type=client_credentials` — caller authenticated by its **SVID over mTLS** (preferred) or
    `client_secret` (dev fallback). Returns the agent's **autonomous** scoped token.
  - `grant_type=urn:ietf:params:oauth:grant-type:token-exchange` (**RFC 8693**) — inputs
    `subject_token` (principal) + `actor_token` (agent SVID/JWT); returns a **delegated** token.
    Requested scope must be ⊆ the actor's allowed scopes ⊆ the subject's scopes.
- `GET /.well-known/jwks.json` — public keys (kid) for verification; the platform trust anchor.
- `POST /clients` (admin) — register `{client_id, spiffe_id, allowed_scopes, secret_hash?}`.
  Bootstrapped for the demo services via an Alembic seed / migration.

**Signing:** one RSA keypair (RS256, `kid`); private key from env/secret (generated + persisted for
local dev), public served via JWKS.

## Token claim model (AIP-shaped, standard JWT)

Standard: `iss`, `aud`, `exp` (short-lived, minutes), `iat`, `jti`, `scope`. Agent-grade:
- `sub` — the principal (user for delegated; the agent for autonomous).
- `act` — nested actor chain (RFC 8693): `{ "sub": "<agent-spiffe-id>", "act": { … } }`.
- `mode` — `"delegated"` | `"autonomous"` (WIMSE distinction).
- `prov` — provenance: `{ request_id, trace_id }` of the originating call (feeds Audit).
- `depth` — delegation depth; verification rejects `depth > max_delegation_depth`.

## `platform-core` integration (the shared-interface change)

- **`config`** — add `auth_mode ∈ {dev, oauth2}`, `oauth2_jwks_url`, `oauth2_issuer`,
  `oauth2_audience`, `max_delegation_depth` (default 3).
- **`auth.py`** — in `oauth2` mode, `require_identity` extracts the Bearer JWT, verifies via a
  **cached JWKS client** (sig + iss/aud/exp + `depth`), and yields
  `CallerIdentity{ id, kind, scopes, mode, on_behalf_of, actor_chain }`. Add a `require_scope("x")`
  dependency factory. `dev` mode is unchanged; the `CallerIdentity` / `require_identity` interface
  stays stable (new fields default empty).
- **`svid.py`** (new) — fetch this workload's X.509 SVID from the Workload API socket (via the
  `spiffe` library), build an **mTLS `httpx` client** from the SVID source, and verify a peer's
  SPIFFE ID. `facade.UpstreamClient` gains an optional `svid_source` for mTLS calls.

## Reference flow (what proves it)

For a `/example/proxy` call, example-service:
1. obtains its **SVID** (Workload API) and a **delegated token** from `identity-service` via
   token-exchange (a simulated principal `subject_token` + its own SVID as `actor_token`);
2. calls upstream-stub over **mTLS**, both verifying peer SPIFFE IDs, passing the delegated token;
3. upstream-stub verifies scope, the `act` chain, and delegation depth.

Logs + one Jaeger trace carry `spiffe_id`, `sub`, and the `act` chain — audit-ready.

## Storage

- **`identity` Postgres DB** — `clients` (client_id, spiffe_id, allowed_scopes, secret_hash?),
  signing key material (or env-provided). Alembic migrations per the Phase 0 pattern.
- **SPIRE server datastore** — SQLite in a volume (SPIRE-internal; not our db-per-service store).

## Infra (compose `identity` profile)

`spire-server`, `spire-agent` (shared socket volume), `spire-registrar` (init), `identity-service`
(+ `identity` DB on the shared Postgres). **Requires the upgraded Docker Engine** — SPIRE agents/
attestors and the added containers are what the old 20.10.x seccomp/thread limits break. The
`identity-service` + `platform-core` work builds and unit-tests before the upgrade; SVID/mTLS
integration verifies after.

## Build order (dependency-ordered)

1. **`identity-service` token core** — RSA keypair, `client_credentials` grant, JWKS, `clients`
   table + migration + seed. *Verify (unit, infra-free):* issue + verify with a local keypair.
2. **Token exchange (RFC 8693)** — delegated grant, `act`/`mode`/`prov`/`depth` claims.
   *Verify (unit):* delegated token shape, scope-narrowing, depth-limit rejection.
3. **`platform-core` `oauth2` mode** — JWKS verification, `require_scope`, depth enforcement,
   `CallerIdentity` fields. *Verify (unit):* verify tokens from step 1–2 with a stub JWKS.
4. **`identity-service` integration** — run it in compose against Postgres. *Verify:* `/oauth/token`
   + JWKS over HTTP; skip-if-down.
5. **SPIRE** — server/agent/registrar in compose; `svid.py`; mTLS on the demo hop.
   *Verify (after Docker upgrade):* SVID issuance; example → upstream over mTLS with a delegated
   token; the end-to-end trace.

## Testing strategy

- **Unit (infra-free):** a locally generated RSA keypair drives token issue/verify, token-exchange,
  delegated-vs-autonomous, depth-limit rejection, JWKS shape, and `platform-core` verification.
- **Integration (skip-if-down):** `identity-service` `/oauth/token` + JWKS against Postgres; the
  SVID-mTLS demo hop once SPIRE is up.

## Risks / watch-items

- **SPIRE on old Docker** — the reason the SPIRE half is gated on the Docker upgrade.
- **X.509-SVID issuance latency** — fine for long-lived services; note **JWT-SVID** as the path for
  ephemeral agent workloads.
- **AIP is draft-00** — we adopt its *concepts* into a standard-JWT model, not its wire format, so we
  don't chase a moving spec; Biscuit is the documented upgrade for offline attenuation / multi-hop.
- **Signing-key management** — env/secret for local dev; a real KMS is out of scope.
- **Delegation-depth default (3)** — configurable; enforced at verify to prevent unbounded chains.
