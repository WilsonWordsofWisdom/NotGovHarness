# Design

Status: **design pass only — nothing in this doc is built yet.** Covers the five new `/ui`
surfaces requested on top of the three already shipped (Skill Registry, Approvals/HITL, Evals
Runner). See [architecture.md](architecture.md#web-ui-layer-design-pass-not-yet-built) for where
each UI sits in the system and [decisions.md](decisions.md) D-096..D-100 for why each screen is
shaped the way it is.

Visual mockups for every screen below: [Platform Console mockups](https://claude.ai/artifact/JwLcw3NEbnKrMUX3mBpP4S)
(private Claude artifact — share it from its Share menu if anyone besides you needs the link).

## Visual language — reuse, not reinvent

This project already has its own small design system, proven across three shipped pages
(`services/skill-registry/static/index.html`, `services/approvals-service/static/index.html`,
`services/evals-runner-service/static/index.html`). Per DESIGN_GUIDE.md's "default to Material
unless a project specifies its own system" — this one already does, so the five new pages match
it rather than importing Material Design components:

- **Color:** CSS custom properties on `:root`, redefined under `prefers-color-scheme: dark` and
  `[data-theme="dark"]`. Accent `#7a5cff` (light) / `#a48bff` (dark); status colors for
  danger/warn/ok, each with a paired `-bg` tint for badges.
- **Type:** system sans (`-apple-system, "Segoe UI", Inter, sans-serif`) for UI text,
  `ui-monospace` for IDs/JSON/tokens.
- **Shape:** flat surfaces on a soft off-white/near-black background, 1px borders, small border
  radius, no elevation/shadow system — this is a utility console, not a marketing surface.
- **Auth:** every page's only credential input is a single bearer-token field (never
  client id/secret) — D-037, reaffirmed by D-099/D-100 for the two services where that rule had
  the most tempting exception.
- **Shared module (proposed, D-097):** `platform-ui.css` + `platform-ui.js`, extracted once 8
  pages exist. Until that lands, the 5 new pages still *look* identical to the 3 existing ones by
  copying the same variables/components, same as those 3 already do with each other.

## Checklist applied to every screen below

Empty / loading / error / success states, responsive down to ~375px width, keyboard + screen
reader (labeled inputs, visible focus ring, `aria-live` region for toast/error messages), and no
animation beyond a short opacity/transform transition on panel switches.

---

## Agent Builder UI — `agent-builder-service`

**Status: built** (`feat/ui-agent-builder`, merged through several corrections — D-101, D-103,
D-104 — each found by reading the real code/library before trusting the previous draft of this
section). Landing view: **Builds** list.

`Build` has **no `status` column** — `POST /builds` either succeeds (kickoff → sign → publish all
in one call) and persists a row, or raises before any row is ever constructed, so a failed
attempt is never listed, only ever returned once as an error response (D-101). Role/goal are
likewise never persisted on a `Build` row — only recoverable from the published Agent Card's
`extensions`, over in Agent Registry (a different service's API). `backstory` is **not a field at
all** in the UI or in `Build`'s schema: it's CrewAI's own required `Agent` constructor field
(confirmed against the installed library, D-104), auto-generated server-side from role/goal when
omitted so the user never has to write one.

Every route on this service (`GET /builds`, `GET /builds/{id}`, `POST /builds`,
`POST /builds/{name}/revise`) requires a valid bearer token — unlike Skill Registry, where
browsing is public. So the page has one bearer-token field used for every call, not one scoped to
a single tab.

1. **Builds list** — table of actually-persisted (successful) builds: name, `card_version`,
   description, agent card id, eval suites (chips), whether a tool was called, created time, and
   an **Edit** action per row. Empty/error states included.
2. **Build detail** — `GET /builds/{id}`'s real fields: name, `card_version`, description,
   `task` (labeled "Instructions" in the UI — the wire field is unchanged), `kickoff_result` (the
   full raw CrewAI output, the only place the agent's actual output is visible), `tool_was_called`,
   `agent_card_id`, `eval_suites`, and an **Edit** button. No status badge — every row here
   already succeeded. A note explains role/goal aren't shown (never persisted here).
3. **New Build form** — bearer-token field, then name/description/instructions/role/goal (no
   backstory field, D-104). Submitting calls `POST /builds` once and shows a spinner — **the
   only state this button has**, since kickoff → sign → publish happens synchronously in that one
   call; there's no separate "trigger" step or live log stream, because neither exists
   server-side. Failure (`kickoff_failed`/`publish_failed`, both 502) shows the real
   `error.code`/`error.message` inline; nothing is added to the Builds list on failure.
4. **Edit (revise) mode** — the same form, triggered from a Builds-list row or the detail view's
   Edit button: name locks to the build being revised, description/instructions pre-fill from its
   latest build, role/goal start blank (not recoverable here, said plainly in the form).
   Submitting calls `POST /builds/{name}/revise` instead — a new, higher-versioned `Build` row
   and Agent Card are created; the one being revised from is left untouched (D-103, matching
   `Build`'s own append-only-audit-trail shape — same pattern as Sandbox's `executions`/
   Guardrails' `checks`).

Non-goals (explicitly not designed): streaming CrewAI logs, a token-minting helper, re-deriving
role/goal from the Agent Registry to pre-fill Edit mode (a same-origin-only `/ui` page calling
another service's API directly was judged out of scope — flagged, not silently done).

## MCP Gateway Registry UI — `mcp-gateway-registry`

(Named Agent Gateway until D-105 — same service, same façade-over-ContextForge shape.)

Landing view: **MCP Servers**. Checked against the real running stack before designing this,
not just the stub tests: `GET /mcp-servers`/`GET /tools` pass through ContextForge's full row
shape (dozens of internal fields — auth credentials, certs, lifecycle bookkeeping), so the UI
is deliberately selective about which fields it surfaces rather than dumping the whole object.
Every route here, reads included, requires a bearer token scoped `mcp_gateway_registry:call` —
same as Agent Builder, there is no public-browse tier.

1. **MCP servers list** — `GET /mcp-servers`, showing: name, url, transport, a status badge
   (combining the real `status`/`enabled`/`reachable` fields — all three are genuinely returned,
   not invented), `toolCount`, `lastSeen`. Empty state: "No MCP servers registered" + CTA to the
   register form.
2. **Register server form** — bearer token + name/url/transport (defaulting to `SSE` per
   `RegisterServerIn`). Calls `POST /mcp-servers`.
3. **Tools** — flat list from `GET /tools` across all registered servers: the federated name
   (`name`, e.g. `mcp-skills-demo-list-skills`), the `originalName` (`list_skills`), which server
   it came from (`gatewaySlug`), description, enabled/reachable. Each row links to the console
   below, pre-selecting that tool.
4. **Tool console** — pick a tool (shows its real `inputSchema` as a read-only reference, not a
   generated form — building a generic JSON-Schema-to-form renderer is out of scope for a
   reference console), a JSON textarea for `arguments` (client-side JSON-validated before
   submit), "Call" button hits `POST /tools/{name}/call`. **The response needs two-level error
   handling, not one**: an HTTP error (401/403/502) is the usual `platform_core` envelope, but a
   *successful* HTTP 200 can still carry a JSON-RPC-level failure (`{"error": {"code",
   "message"}}` with no `"result"` key) when the tool call itself failed — confirmed against the
   real service, not assumed. The UI checks for `body.error` even on a 200 before treating the
   call as a success. This is a test console, not an admin surface for ContextForge itself — no
   iframe/link to ContextForge's own `:4444` admin UI, which stays an internal, non-public
   debugging surface (D-098).

## Eval Registry UI — `eval-registry`

Landing view: **Suites**. Corrected against the real running service before writing the page
(D-107): **browsing is fully public here** — `GET /suites`/`GET /suites/{name}[/{version}]`/
`GET /suites/{name}/{version}/dataset` have no `Depends(require_identity)` at all, same posture
as Skill Registry, unlike Agent Builder/MCP Gateway Registry where every route needs a token.
Only `POST /suites` needs a bearer token (`eval_registry:publish`). And **`GET /suites` returns
no version field at all** — it's deduped to one row per name (`{name, description, kind,
applies_to}`), already collapsed to the latest; a version only appears once you open a suite's
detail. The earlier design draft's "name, latest version, kind, applies_to" list column assumed
a field the list response doesn't have.

1. **Suites list** — `GET /suites`: name, description, kind badge (`cases`/`redteam`),
   applies_to tags. No bearer token needed to view this. Row click → detail (which does carry
   version, fetched separately via `GET /suites/{name}`).
2. **Suite detail** — kind-specific body, confirmed from a real published suite (47 live, all
   `kind=cases`; no `redteam` suite exists anywhere in this platform yet, nothing publishes one):
   `cases` suites show the metrics envelope (`engine`/`metric_id`/`params` — real engines are
   `deepeval`/`ragas`/`promptfoo`/`custom`, per `KNOWN_ENGINES`) and a "Download dataset" button;
   `redteam` suites would show `redteam_config.purpose` (required) and whatever else is in that
   object instead of a dataset. Also shows `scan_findings` (D-038/D-041's judge-rubric scan
   warnings) when any exist — real field, empty list on every suite checked so far, not
   something the earlier draft mentioned at all.
3. **Publish form** — bearer token + suite metadata fields + kind selector that swaps the form
   body (dataset upload for `cases`, generation-config fields for `redteam`). The request is
   **multipart**, not JSON: a `metadata` form field holding the JSON-encoded metadata string,
   plus a `dataset` file field for `cases` suites (newline-delimited JSON goldens:
   `{input, expected_output?, context?, expected_tools?, metadata}` per line) — same shape as
   Skill Registry's existing publish UI, confirmed against `publish_suite`'s actual
   `Form(...)`/`UploadFile` signature, not assumed from the metadata schema alone.

## Agent Registry UI — `agent-registry`

Landing view: **Agents** (browse), not publish — publishing is normally done by Agent Builder.

1. **Agents list** — grouped by name, each with its published versions (`GET /agents`).
2. **Card detail** — full card rendered as labeled fields (name, url, version, capabilities) plus
   a collapsible "raw JSON" block; a **Verify** button calls
   `GET /agents/{name}/{version}/verify` and shows a clear valid/invalid result, including the
   signing key id — this is the one action every card supports, so it's the primary CTA on the
   page, not buried.
3. **Manual publish (Advanced tab)** — secondary, visually de-emphasized tab: bearer token + raw
   card JSON → calls `identity-service POST /cards/sign` then `agent-registry POST /agents` with
   the returned signature, mirroring Skill Registry's publish flow. Labeled "normally agents are
   published automatically by Agent Builder — use this only to publish a hand-authored card" so
   it doesn't read as the main path (D-100).

## Identity Service UI — `identity-service`

Landing view: **Clients**.

1. **Clients list** — `client_id` + `allowed_scopes` from `GET /clients`. No secret is ever shown
   here (the API never returns one).
2. **Register client form** — `client_id`, a **password-type** secret field with inline copy
   text "this is hashed and stored — it will not be shown again, save it now," and an
   `allowed_scopes` input. Calls `POST /clients`.
3. **Signing keys** — read-only view of `GET /.well-known/jwks.json` (key id, algorithm, use),
   so an operator can confirm which key is currently active without needing a token-minting flow
   to find out.

**Deliberately not designed:** a "get a token" playground (paste `client_id`/`client_secret`,
receive a bearer token). `POST /oauth/token` needs exactly the client-secret-in-a-browser-form
pattern D-037 already rejected for every other registry; adding it to the one service that
actually owns these secrets would be the same anti-pattern with higher stakes (D-099). Operators
mint tokens via curl/httpx, as every other flow in this platform already does.
