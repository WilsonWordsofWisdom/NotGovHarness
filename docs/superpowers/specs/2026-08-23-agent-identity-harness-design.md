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
- **`spire-bootstrap`** — a one-shot init container (`docker:cli`, not the SPIRE image — see
  below) running `spire-server token generate` + `entry create` to map each service's compose
  selector (`docker:label:com.docker.compose.service:<svc>`) → `spiffe://notgovharness/<svc>`.
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

`spire-server`, `spire-agent` (shared socket volume), `spire-bootstrap` (init), `identity-service`
(+ `identity` DB on the shared Postgres). The Docker upgrade this section originally gated SPIRE
on had already happened by the time this got built (see the Observability harness's learnings) —
ClickHouse, MinIO, Redis, and now SPIRE all ran clean on the current engine with no seccomp
workaround needed.

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

- **X.509-SVID issuance latency** — fine for long-lived services; note **JWT-SVID** as the path for
  ephemeral agent workloads.
- **AIP is draft-00** — we adopt its *concepts* into a standard-JWT model, not its wire format, so we
  don't chase a moving spec; Biscuit is the documented upgrade for offline attenuation / multi-hop.
- **Signing-key management** — env/secret for local dev; a real KMS is out of scope.
- **Delegation-depth default (3)** — configurable; enforced at verify to prevent unbounded chains.
- **join_token ignores an explicit `-spiffeID`** — confirmed against a real attest log: the
  resulting node's SPIFFE ID is always `spiffe://notgovharness/spire/agent/join_token/<the token
  itself>`, not whatever `-spiffeID` was passed to `token generate`. `entry create`'s `-parentID`
  must use that exact derived ID or the agent never receives the entries — cost a full debug cycle
  to find.
- **join_token is single-use** — every fresh token generation therefore mints a *new* agent node
  ID, so entries registered against a previous run become orphaned on agent restart unless
  spire-bootstrap re-runs first (it does, on every `docker compose up`, since it's a one-shot
  gating spire-agent via `condition: service_completed_successfully`). A `docker compose restart
  spire-agent` alone, without re-running bootstrap, will fail to re-attest — a known, accepted
  limitation for a local reference platform rather than building persistent node credentials.
- **The spire-server/spire-agent images ship no shell** — only the Go binaries. `spire-bootstrap`
  therefore runs `docker exec` into the (named, `container_name: spire-server`) server container
  from a sibling `docker:cli` container instead of scripting inside the SPIRE image directly.
- **Fresh named volumes are root-owned; spire-server's image defaults to UID 1000** — sqlite failed
  to create its datastore file with a "no such file or directory" error that was actually a
  permission error in disguise. Fixed with `user: "0:0"` on spire-server (spire-agent already
  defaults to root, unaffected).
- **`token generate` without `-spiffeID` prints a stray "Warning: Missing SPIFFE ID." line to
  stdout** — corrupted a naive `sed`-only token extraction (embedded newline + trailing text).
  Fixed by `grep '^Token:'` before the `sed`, so only the real token line is captured regardless
  of what else the CLI prints.
- **The Docker workload attestor needs `pid: host` on spire-agent** — without it, every attestation
  failed with "could not resolve caller information": the attestor resolves a caller's PID (from
  the Workload API socket's peer credentials) to a container via `/proc/<pid>/cgroup` on its *own*
  filesystem view, and a PID from a workload's separate PID namespace is meaningless there. Only
  the agent needs `pid: host` — Linux translates peer credentials into the receiver's namespace
  view automatically for nested namespaces, so example-service/upstream-stub keep their own
  isolated PID namespaces.
- **uvicorn logs `https://` even with a client cert requirement misconfigured** — its startup
  banner only reflects whether `ssl_certfile` was set, not whether the handshake will actually
  succeed; the real proof this harness relies on is a live request landing (or a plain-HTTP
  negative control failing against the same port), not the banner text.
