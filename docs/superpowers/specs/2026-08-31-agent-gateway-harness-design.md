# Agent Gateway Harness — Design

**Status:** built and verified live end to end — all 5 done-when criteria confirmed against the
actual running stack: a real bearer token from identity-service registers the real
`mcp-skills-demo` MCP server with a real ContextForge through the `agent-gateway` façade, then
calls its `list_skills` tool through that same façade and gets back real Skill Registry data.
Not yet merged to `main`.
**Date:** 2026-08-31
**Wave:** 3 (Runtime & Policy) · first
**Branch:** `feat/wave3-agent-gateway`

Related: [../../architecture.md](../../architecture.md) ·
[../../decisions.md](../../decisions.md) (D-008, D-017) ·
[../../implementation-plan.md](../../implementation-plan.md)

## Context

Decisions table: **Agent Gateway | IBM ContextForge (Python/FastAPI) | Apache-2.0 | Integrate |
Postgres.** D-017: ContextForge "already federates A2A + MCP + REST/gRPC, so it becomes the
single agent-level control point (agent↔agent and agent↔tool traffic, policy, cost, audit)."
Architecture's connector edges: `Agent Runtime → Agent Gateway → MCP servers / other agents
(A2A)`.

**Researched against the current repo** (`IBM/mcp-context-forge`, now branded "ContextForge",
1.0.0 GA) rather than assumed:

- **MCP federation is fully implemented**: register an upstream MCP server (`POST /gateways`),
  ContextForge proxies `tools/call` JSON-RPC to it via `/rpc`, translating between stdio/SSE/
  streamable-HTTP transports.
- **A2A support is real, shipped, but narrower than full bidirectional federation**: it registers
  external A2A agents and converts their declared skills into MCP tools — "A2A agent as an MCP
  tool," not two-way agent-to-agent routing.
- **Virtual servers** (`POST /servers`) compose a named, curated subset of registered tools —
  the mechanism for exposing a per-team/per-agent tool slice later.
- Ships its own Postgres-or-SQLite storage, its own admin UI (`/admin`), its own JWT/API-key
  auth, and a `GET /health` check.

## Auth model decision — this harness's real design question

ContextForge *can* validate bearer tokens from an external OAuth2/OIDC issuer (added for exactly
this "reference-platform has its own identity provider" case — config flags
`SSO_API_TOKEN_AUTH_ENABLED` + a per-provider `trusted_for_api_auth` DB flag, generic-OIDC
provider registration via `SSO_GENERIC_ISSUER`/`SSO_GENERIC_JWKS_URI`). **Considered and
rejected for this harness:**

1. It requires registering identity-service as a full "Generic OIDC provider," which needs a
   working `authorization_url` (browser-redirect login endpoint) — identity-service only
   implements `client_credentials` + RFC 8693 token-exchange grants, no authorization-code flow.
   Building one just to satisfy ContextForge's provider-registration model, for a code path we'd
   never actually use (browser login), is real unforced work.
2. It's opt-in and has at least one documented open rough edge (external-IdP validation is wired
   into the `/mcp` path's middleware, not the session-affinity-routed `/rpc` path — not something
   this single-instance reference deployment would hit, but a sign the feature is newer/less
   hardened than ContextForge's own native auth).

**Decision: standard façade pattern, not external-IdP registration.** Matches architecture.md's
own definition verbatim: a façade "wraps an upstream OSS project... behind the platform's OpenAPI
contract + identity + OTel + events." A new `agent-gateway` FastAPI service sits in front of the
ContextForge container: **our** identity-service gates every caller (`require_scope(...,
"agent_gateway:call")`, same hybrid pattern every other service uses), and the façade calls
ContextForge's own API using ContextForge's own bootstrap admin credential — a backend-only,
service-to-service hop callers never see. Same shape as how Langfuse's own API key pair is used
in the Observability harness: the upstream tool's native credential is an implementation detail
behind our identity boundary, not something we try to unify with our own OAuth2 issuer.

## Goal & success criteria

Prove the whole chain works, not just that a container starts: `agent-gateway` (our identity) →
ContextForge (MCP federation) → a real MCP server → real data from an already-built harness.

**Done when:**
1. ContextForge runs in compose, backed by its own Postgres `agent_gateway` DB (db-per-service,
   same as every other harness — not the SQLite default, for consistency with D-004).
2. `agent-gateway` façade (scope-gated `agent_gateway:call`) exposes: register an MCP server,
   list registered servers, list available tools, call a tool — proxying to ContextForge using
   its bootstrap admin credential.
3. A minimal demo MCP server (new, small — wraps Skill Registry's `GET /skills` as one MCP tool,
   using the official `mcp` Python SDK) is registered with ContextForge through the façade.
4. Calling that tool through `agent-gateway`'s tool-call endpoint returns **real Skill Registry
   data**, round-tripped through the whole chain: façade → ContextForge → demo MCP server →
   Skill Registry → back. Not a mock, not a container-is-up check.
5. Unit tests (infra-free: façade request/response shaping against a stub ContextForge) +
   integration tests (skip-if-down: real ContextForge + Skill Registry) green; `task lint` clean.

## Non-goals (YAGNI)

**Virtual servers** (`POST /servers`) — no consumer needs curated per-agent tool subsets yet
(that's Agent Builder, Wave 4); the registry/proxy mechanism is what this harness needs to prove.
**A2A-agent-as-MCP-tool registration** — real ContextForge capability, but nothing in this
platform is an A2A-callable running agent yet (Agent Builder/Runtime, Wave 4) — same "consumer
doesn't exist yet" relationship every registry has had. **Policy/cost/audit enforcement at the
gateway** (D-017 names this as a longer-term Agent Gateway responsibility) — this harness proves
the routing mechanism; policy enforcement is real future work, not invented here. **Multi-replica
ContextForge / Redis** — single-instance reference deployment, no session affinity to worry about.
**External-OIDC token validation inside ContextForge** — see the Auth model section above.

## Components

- **`contextforge`** (integrated, not built) — `ghcr.io/ibm/mcp-context-forge` container.
  Postgres `agent_gateway` DB. Bootstrap admin credential (`PLATFORM_ADMIN_EMAIL`/
  `_PASSWORD`) generated locally, never committed — same posture as every other local-dev secret
  in this repo.
- **`agent-gateway`** (new façade service) — greenfield-shaped FastAPI wrapping ContextForge,
  per architecture.md's façade definition. Gates callers with our identity-service; holds
  ContextForge's admin credential as its own backend secret.
- **`mcp-skills-demo`** (new, minimal) — a small MCP server (official `mcp` Python SDK) exposing
  one tool, `list_skills`, that calls Skill Registry's `GET /skills`. Exists purely to give this
  harness something real to federate and prove the chain against — analogous to how `upstream-
  stub` exists for the façade demo in Phase 0.

## API (agent-gateway façade)

- `POST /mcp-servers` (scope `agent_gateway:call`) — register an upstream MCP server with
  ContextForge (`{name, url, transport}`); proxies to ContextForge's `POST /gateways`.
- `GET /mcp-servers` — list registered servers.
- `GET /tools` — list tools available across federated servers.
- `POST /tools/{name}/call` — call a tool; proxies to ContextForge's `/rpc` (`tools/call`).

## Build order (dependency-ordered)

1. **ContextForge compose integration** — container, Postgres `agent_gateway` DB, bootstrap
   admin credential in local `.env` (gitignored). *Verify:* `GET /health` on the ContextForge
   container itself, real, no façade involved yet.
2. **`agent-gateway` façade scaffold** — identity-gated, holds the ContextForge admin credential,
   proxies register/list-servers. *Verify (unit):* against a stub ContextForge HTTP server.
3. **`mcp-skills-demo`** — minimal MCP server wrapping Skill Registry's `GET /skills`. *Verify:*
   runs and responds to a raw MCP `tools/list`/`tools/call` request directly (no gateway yet).
4. **Tool listing + call proxy** — `GET /tools`, `POST /tools/{name}/call` on the façade.
   *Verify (unit):* against a stub ContextForge.
5. **Compose integration + live test** — register `mcp-skills-demo` with a running ContextForge
   through a running `agent-gateway`, call `list_skills` through the façade, confirm the response
   is real Skill Registry data (publish a real skill first, then see it come back through the
   whole chain). *Verify:* skip-if-down, matching the established pattern.

## Testing strategy

- **Unit (infra-free):** façade request/response shaping against a stub ContextForge HTTP
  server (same technique used for stub JWKS servers elsewhere in this repo).
- **Integration (skip-if-down):** the full register → list → call loop against real ContextForge
  + Skill Registry, per step 5.

## Risks / watch-items

- **ContextForge is a large, actively-changing project (1.0.0 GA, but young).** This harness
  only exercises a narrow slice of its surface (gateway registration + tool proxy); its admin UI,
  virtual servers, policy features, and A2A-agent registration are all real but unused here.
- **Bootstrap admin credential management is manual for this reference deployment** — generated
  once via `PLATFORM_ADMIN_EMAIL`/`_PASSWORD` at container start, stored in local `.env` like
  every other local-dev secret. A production deployment would want ContextForge's own API-key
  issuance flow wired into a proper secrets manager; out of scope for a reference platform.
- **`PLATFORM_ADMIN_EMAIL` must not use a reserved/special-use TLD** (found live: `.local`,
  matching this repo's `*.notgovharness.local` naming convention used everywhere else, is
  rejected outright by ContextForge's email validator — `python-email-validator` treats it as a
  special-use domain, not a config bug on our side). Used `admin@example.com` (RFC 2606) instead.
  Cost a real debug cycle (generic `EXPOSE_ERROR_DETAILS=false` 422s hid the actual validation
  message until that flag was flipped on temporarily to diagnose it).
- **`PASSWORD_CHANGE_ENFORCEMENT_ENABLED=false` is required for a service-credential bootstrap
  admin account** (found live) — otherwise first login after bootstrap returns 403
  "password change required," which makes no sense for a credential a façade holds and never
  logs in interactively with more than once.
- **Postgres needs the `+psycopg` driver suffix** (`postgresql+psycopg://...`) — plain
  `postgresql://` defaults to `psycopg2`, which isn't installed in the ContextForge image
  (found live: `ModuleNotFoundError: No module named 'psycopg2'`).
- **SSRF protection blocks registering any gateway on the compose network by default** (found
  live) — every MCP server in this reference platform genuinely lives on a private/RFC1918-ish
  Docker network, which is exactly what ContextForge's default SSRF protection exists to block.
  `SSRF_ALLOW_PRIVATE_NETWORKS=true` is the documented flag for this case (not a blanket bypass —
  `SSRF_BLOCKED_NETWORKS`/`_HOSTS`, e.g. cloud metadata endpoints, still apply).
- **`mcp-skills-demo` needed DNS-rebinding protection disabled** (found live: a real request
  came back `421 Misdirected Request`) — the `mcp` SDK's streamable-HTTP transport validates the
  incoming `Host` header against an allowlist by default; ContextForge connects via the Docker
  network's service-name hostname (`mcp-skills-demo:8000`), not `localhost`, which the default
  allowlist doesn't include. Reasonable trade-off for a service never reached outside the
  compose-internal network; `TransportSecuritySettings(enable_dns_rebinding_protection=False)`.
- **ContextForge federates a tool under `{gateway_slug}-{tool_name}`, not the tool's own bare
  name** (found live: calling `/tools/list_skills/call` 404s with "Tool not found"; the actual
  callable name from `GET /tools` was `mcp-skills-demo-list-skills`). A caller of the façade has
  to use the federated name, not assume it matches whatever the MCP server itself calls the tool.
- **A ContextForge session can be invalidated (e.g. by its own restart) before the façade's
  client-side expiry clock would think to refresh it** (found live, D-046) — the façade's cached
  token kept getting rejected with 401 after ContextForge was rebuilt, even though nothing in the
  façade's own `expires_in`-based timer said it should be stale. Fixed with a retry-once-on-401
  in `ContextForgeClient._request`, re-logging in and retrying rather than surfacing a spurious
  auth failure to the façade's own caller for something that isn't their fault.
