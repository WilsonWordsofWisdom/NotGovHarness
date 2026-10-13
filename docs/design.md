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

Landing view: **Builds** list. Revised while building, against `models.py`/`main.py` directly
(not just the earlier research pass): a `Build` row has **no `status` column and no
`role`/`goal`/`backstory` columns** — `POST /builds` either succeeds (kickoff → sign → publish
all in one call) and persists a row, or raises before any row is ever constructed, so a failed
attempt is never listed at all, only ever returned once, as an error response to whoever
submitted it. The earlier draft of this section assumed a `pending`/`running`/`failed` status and
a stored role/goal/backstory on every build — both wrong; corrected here before writing the real
page, and logged as D-101 rather than silently fixed.

Every route on this service (`GET /builds`, `GET /builds/{id}`, `POST /builds`) requires a valid
bearer token (`require_identity`; `POST` additionally needs `builder:build` scope) — unlike Skill
Registry, where browsing is public. So the page gets one bearer-token field used for every call,
not one scoped to a "New build" tab only.

1. **Builds list** — table of actually-persisted (i.e. successful) builds: name, description,
   agent card id, eval suites (chips), whether a tool was called (`tool_was_called`), created
   time. Empty state: "No builds yet" + pointer to the New Build tab. Row click → detail.
2. **Build detail** — `GET /builds/{id}`'s real fields: name, description, task,
   `kickoff_result` (the full raw CrewAI output, shown as a monospace block — the only place the
   agent's actual output is visible), `tool_was_called`, `agent_card_id`, `eval_suites`.
   **Role/goal/backstory are not shown here** — never persisted on this row; they're only
   recoverable from the published Agent Card's `extensions` block, which belongs to the Agent
   Registry UI, not this one. No status badge — every row here already succeeded.
3. **New Build form** — bearer-token field, then name/description/task/role/goal/backstory text
   inputs (all required by `BuildIn`). Submitting calls `POST /builds` once and shows a
   spinner — **the only state this button has**, because the backend runs kickoff → sign →
   publish synchronously in that one call; the UI does not pretend there's a separate "trigger"
   or a live log stream, since neither exists server-side. Success routes to the new build's
   detail view. Failure (`kickoff_failed` or `publish_failed`, both HTTP 502) shows the real
   `error.code`/`error.message` from `platform_core.errors`'s envelope inline — and nothing is
   added to the Builds list, since the backend never persisted anything.

Non-goals (explicitly not designed): editing a build, re-running a build, streaming CrewAI logs,
showing role/goal/backstory on the build itself — none of these exist in the API today, and the
brief is "design what can actually ship."

## Agent Gateway UI (MCP registry) — `agent-gateway`

Landing view: **MCP Servers**.

1. **MCP servers list** — registered servers (name, url, transport) with a status chip from
   `GET /mcp-servers`. Empty state: "No MCP servers registered" + CTA to the register form.
2. **Register server form** — bearer token + name/url/transport (defaulting to `SSE` per
   `RegisterServerIn`). Calls `POST /mcp-servers`.
3. **Tools** — flat list from `GET /tools` across all registered servers, each showing its
   federated (prefixed) name.
4. **Tool console** — pick a tool, a JSON textarea for `arguments` (client-side JSON-validated
   before submit, inline parse-error state if invalid), "Call" button hits
   `POST /tools/{name}/call`, result rendered as formatted JSON with a copy button. This is a
   test console, not an admin surface for ContextForge itself — no iframe/link to ContextForge's
   own `:4444` admin UI, which stays an internal, non-public debugging surface (D-098).

## Eval Registry UI — `eval-registry`

Landing view: **Suites**.

1. **Suites list** — name, latest version, kind badge (`cases`/`redteam`), applies_to tags.
2. **Suite detail** — metadata (description, applies_to), kind-specific body: `cases` suites show
   the metrics envelope (`engine` / `metric_id` / `params` chips) and a "Download dataset" button
   (`GET /suites/{name}/{version}/dataset`); `redteam` suites show the generation config
   (`purpose`/`plugins`/`strategies`) instead of a dataset — the two kinds get visibly different
   detail layouts rather than one layout with blank fields (matches D-040's own "two real kinds,
   not one shape stretched to fit both").
3. **Publish form** — bearer token + suite metadata fields + kind selector that swaps the form
   body (dataset upload for `cases`, generation-config fields for `redteam`). Same pasted-token
   shape as Skill Registry's existing publish UI.

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
