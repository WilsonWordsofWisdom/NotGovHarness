# Skill Registry Harness — Design

**Status:** built and verified — all 6 done-when criteria confirmed live: a real zip bundle,
signed by nothing (the standard has no signing concept) but scope-gated, was published through a
running identity-service + skill-registry, fetched back byte-for-byte (metadata and the raw
archive download), and every rejection path (bad name pattern, path-traversal entry) returned a
clear 422, not a 500. Not yet merged to `main`.
**Date:** 2026-08-30
**Wave:** 2 (Catalog & Registries) · second of three
**Branch:** `feat/wave2-skill-registry`

Related: [../../architecture.md](../../architecture.md) ·
[../../decisions.md](../../decisions.md) · [../../implementation-plan.md](../../implementation-plan.md)

## Context

Decisions table: **Skill Registry | Agent Skills standard (`SKILL.md`, agentskills.io) | Open |
Build to standard | Postgres + MinIO.** Confirmed against the current spec
(agentskills.io/specification, standard originally published by Anthropic): a **skill** is a
directory containing, at minimum, a `SKILL.md` file — YAML frontmatter (`name`, `description`
required; `license`, `compatibility`, `metadata`, `allowed-tools` optional) followed by Markdown
instructions — plus optional `scripts/`, `references/`, `assets/` subdirectories the agent loads
on demand.

**The standard's own design principle — progressive disclosure — maps directly onto a registry
read API:** agents are meant to load only `name`+`description` at discovery time (~100 tokens),
the full `SKILL.md` body only once a skill is activated, and bundled files only as the task
needs them. `GET /skills` (list) and `GET /skills/{name}` (full card) mirror those two stages
exactly, rather than being an arbitrary API shape layered on top.

**What's *not* in the standard:** a version field. `SKILL.md` frontmatter has no `version` key
(the spec's own example puts `version: "1.0"` inside the free-form `metadata` map, not as a
first-class field). Re-publishing the same skill name is a registry concern, not a standard one —
this harness requires an explicit `version` string at publish time, the same shape Agent Registry
already uses for `(name, version)` uniqueness, kept as a registry-level addition on top of the
standard rather than smuggled into `SKILL.md` itself.

**No signing.** Unlike A2A Agent Cards, the Agent Skills spec has no signature concept — validation
is purely structural (the naming/length/character rules below), checked the same way the reference
`skills-ref validate` tool checks them. This harness hand-implements those same rules rather than
shelling out to that tool, since they're simple enough to state directly and this avoids a new
external dependency.

## Goal & success criteria

Publish, validate, store, and serve `SKILL.md`-format skill bundles, proving the registry can
enforce the standard's structural rules and serve both progressive-disclosure stages.

**Done when:**
1. `POST /skills` (scope-gated `skill_registry:publish`) accepts a zip archive of a skill
   directory + a `version` string, validates the `SKILL.md` frontmatter against every rule in the
   spec's table (below), and rejects anything that violates one.
2. Valid, accepted skills are stored: frontmatter + `SKILL.md` body in Postgres (fast, queryable,
   directly servable); the full bundle archive in MinIO (script/reference/asset files, downloaded
   only when needed — matching "Execution").
3. `GET /skills` returns only `name` + `description` per skill (Discovery). `GET /skills/{name}`
   (latest) and `GET /skills/{name}/{version}` return the full frontmatter + `SKILL.md` body
   (Activation). `GET /skills/{name}/{version}/bundle` streams the archive (Execution).
4. A skill whose `SKILL.md` violates a naming/length rule (bad `name` pattern, oversized
   `description`, directory name not matching frontmatter `name`) is rejected with a clear reason,
   not silently accepted or a 500.
5. A real zip bundle published through a running skill-registry is fetched back — frontmatter and
   `SKILL.md` body byte-for-byte, and the downloaded bundle archive matches what was uploaded.
6. Unit tests (infra-free: frontmatter validation against the spec's own valid/invalid examples) +
   integration tests (skip-if-down: real Postgres + MinIO) green; `task lint` clean.

## Non-goals (YAGNI)

Skill *execution* or sandboxing (a different, not-yet-built harness's concern — this is a catalog,
not a runtime). Semantic/embedding search over skills (name/description substring filtering is
enough for a reference catalog, same posture as Agent Registry's `?skill=` filter). Dependency
resolution between skills. Shelling out to the external `skills-ref` CLI (the validation rules are
simple enough to hand-implement; see Context). Signing (the standard has none). Content-quality
linting of *why* a description is good (the spec's "should describe what and when" guidance is a
SHOULD, not a MUST — only structural constraints are enforced, matching what `skills-ref validate`
itself checks).

## Components

- **`skill-registry`** (new) — greenfield FastAPI (façade-free, like the other registries).
  Postgres `skill_registry` DB (metadata + `SKILL.md` body) + MinIO bucket `skill-registry` (full
  bundle archives). No Kafka consumer — catalog CRUD, not an event listener.
- **`platform_core.objectstore`** (new, shared) — a minimal MinIO wrapper (`ensure_bucket`,
  `put_object`, `get_object`), the same shape as `platform_core.db.Database` (construct,
  lifespan-manage, hand out to routes). Built in `platform-core`, not `skill-registry`, because
  Eval Registry — the very next Wave 2 harness — also needs MinIO (decisions table); this isn't
  speculative reuse, it's the named next consumer.

## `SKILL.md` frontmatter validation (verbatim from the spec)

| Field | Required | Rule |
|---|---|---|
| `name` | Yes | 1–64 chars; lowercase alphanumeric + hyphens only; no leading/trailing hyphen; no consecutive hyphens; **must equal the zip's top-level directory name**. |
| `description` | Yes | 1–1024 chars, non-empty. |
| `license` | No | Free-form string. |
| `compatibility` | No | 1–500 chars if present. |
| `metadata` | No | Map of string → string. |
| `allowed-tools` | No | Space-separated string. |

Registry-level addition (not part of the standard): `version` — a non-empty string, supplied
alongside the upload, `(name, version)` unique — same shape as Agent Registry's card versioning.

## API

- `POST /skills` (scope `skill_registry:publish`) — multipart: `file` (zip archive of the skill
  directory), `version` (form field). Validates, stores, upserts on `(name, version)`.
- `GET /skills?q=` — list: `[{name, description}]` only (Discovery stage). Optional substring
  filter over name/description.
- `GET /skills/{name}` — latest version's full frontmatter + `SKILL.md` body (Activation stage).
- `GET /skills/{name}/{version}` — one version's full frontmatter + body.
- `GET /skills/{name}/{version}/bundle` — streams the raw zip archive (Execution stage).

## Storage

- **`skill_registry` Postgres DB** — one `skills` table: `id` (serial PK), `name`, `version`
  (unique together), `description`, `license`, `compatibility`, `metadata` (jsonb), `allowed_tools`,
  `skill_md` (text — the exact uploaded `SKILL.md` content, frontmatter + body, the direct source
  an agent loads at Activation), `bundle_object_key` (text — the MinIO key), `bundle_size_bytes`,
  `published_by`, `created_at`. Alembic migration, existing per-service pattern.
- **MinIO bucket `skill-registry`** — one object per `(name, version)`: key `{name}/{version}.zip`,
  the raw uploaded archive.

## Build order (dependency-ordered)

1. **Frontmatter validation + `platform_core.objectstore`** — pure validation function against
   every rule in the table above; `ObjectStore` wrapper (`ensure_bucket`/`put_object`/
   `get_object`). *Verify (unit, infra-free):* the spec's own valid/invalid `name`/`description`
   examples pass/fail as expected.
2. **`skill-registry` scaffold + migration** — copier-scaffolded service, `skills` table, MinIO
   bucket-ensure on startup. *Verify:* migration applies cleanly; bucket-ensure is idempotent.
3. **Publish endpoint** — zip upload, unzip-and-validate (path-traversal and size-capped — a
   reference platform accepting arbitrary uploaded archives should reject `..` entries and an
   oversized payload, basic hygiene not scope creep), store in Postgres + MinIO; `skill_registry
   :publish` scope seeded on the `example-service` demo client (same "simulated principal"
   pattern as D-030 — a skill author, this time). *Verify (unit):* valid bundle accepted; bad
   `name` pattern, mismatched directory name, oversized `description`, and a path-traversal entry
   are all rejected with a clear reason, not a 500.
4. **Read endpoints** — list (Discovery shape)/get/version/bundle download. *Verify (unit):*
   against seeded rows and a real MinIO bundle round-trip.
5. **Compose integration + live test** — wire `skill-registry` into `docker-compose.yml` (new
   `registry` profile — already used by Agent Registry, `objectstore` for MinIO), publish a real
   zip through a running service, fetch it back byte-for-byte, download the bundle, confirm it
   matches the upload. *Verify:* skip-if-down, matching the established pattern.

## Testing strategy

- **Unit (infra-free):** frontmatter validation against the spec's own examples — no Postgres or
  MinIO needed to prove the structural rules are enforced correctly.
- **Integration (skip-if-down):** full publish → fetch → bundle-download loop against real
  Postgres + MinIO + identity-service, per step 5.

## Risks / watch-items

- **Zip bomb / path traversal on upload.** An uploaded archive is untrusted input; without caps,
  a malicious or malformed zip could exhaust disk/memory (a bomb) or write outside the intended
  prefix (`../../etc/passwd`-style entries). Mitigated with a max uncompressed-size cap and a
  reject-any-`..`-entry check before extracting anything — see step 3.
- **No cryptographic integrity on bundles**, unlike Agent Registry's signed cards — the standard
  has no signing concept, so `bundle_object_key` content is only as trustworthy as whoever has
  `skill_registry:publish`. Acceptable for a reference platform; a production deployment might
  layer checksums or signing on top, out of scope here.
