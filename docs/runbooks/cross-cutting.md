# Cross-Cutting Runbooks

Read this file first, before any layer file — five issues recur across nearly every one of the
16 services, and understanding them once here saves re-learning them per service.

## Starting, stopping, and inspecting the stack

Everything runs via one `docker-compose.yml` file at the project's root, gated behind
**profiles** (named on/off switches) — nothing comes up with a bare `docker compose up`.
Bringing up the entire platform at once means turning on every profile:

```bash
docker compose --profile core --profile identity --profile registry --profile objectstore \
  --profile gateway --profile temporal --profile approvals --profile sandbox --profile guardrails \
  --profile llm --profile memory --profile knowledge --profile builder --profile evals \
  --profile deployment --profile audit --profile clickhouse --profile observability up -d
```

Each layer file in this directory lists the *minimal* profile set that specific service needs,
if you only want to run part of the platform.

**Checking what's actually running:**

```bash
docker compose ps                     # which containers are up
docker compose logs -f <service>      # watch one service's log output live
curl http://localhost:<port>/healthz  # every service answers this, no login needed
```

**If a service won't start on a completely fresh setup:** check its database was actually
created (`infra/db/init/01-create-databases.sql` lists every service's own database — a missing
entry there is the usual cause). Database setup only runs once, against a brand-new, empty
storage volume — if you change that file after the volume already exists, you'll need to wipe
the volume for it to take effect.

## The Docker-networking failure class

This is the single most-repeated mistake across this platform's entire construction — made and
fixed four separate times, in three different services, by three different people's worth of
debugging effort even though it's really the same one confusion each time. If you see an error
like `Name or service not known` or `Connection refused`, or something that works when you test
it one way but not another, check here first.

**The core confusion, explained plainly: there are three different meanings of "this computer"
once Docker is involved.**

1. **Your own laptop's `localhost`** — what you mean when you run a command in your own
   terminal.
2. **A container's own `localhost`** — refers to *that one container only*, not your laptop and
   not any other container. Code running *inside* a container that tries to reach `localhost` is
   talking to itself, not whatever it actually meant to reach.
3. **`host.docker.internal`** — a special address Docker provides specifically so code *inside*
   a container can reach something published on your actual laptop (including something another
   container published there).

**The rule of thumb:** code running inside a container needs `host.docker.internal` to reach
something published on the laptop — never `localhost`. Code running inside a container needs
another container's own name (like `identity-service`) to reach that container directly — not
`host.docker.internal`, not `localhost` either. Which one is correct depends entirely on *where
the code asking the question actually runs*, which is easy to get wrong because it's not always
obvious from reading the code alone.

**A second, related trap:** when one service's own code tells Docker to start up *another*
container on the fly (Deployment Pipeline does this to start a deployed agent), that new
container needs to be explicitly told which network to join. Forgetting this step is subtle —
the new container comes up looking perfectly healthy (its own self-check doesn't need to talk to
anyone else), but it then can't reach any other service by name at all. The fix is a specific,
explicit setting telling Docker which network to actually use — already applied correctly in
this codebase; if you're writing new code that starts a container this way, copy that exact
pattern.

**If you're debugging a container-to-container call that's failing:**

1. Is the code that's making the failing call running inside a container, or on your own laptop
   directly?
2. If it's inside a container calling *another service in this platform*: use that service's own
   name, not `localhost` or `host.docker.internal`.
3. If it's inside a container calling something published on the laptop (including a container
   that was just started): use `host.docker.internal`.
4. If the target is a container that was started programmatically (not via the standard startup
   file): confirm it actually joined the right network, don't just assume it did.
5. Always read the actual error message from the container that's making the failing call before
   guessing at a fix — every single instance of this mistake in this platform was found this
   way, never by assuming.

## Token TTLs and auth failures

Every "badge" (login token) Agent Identity issues expires after **5 minutes**, on purpose —
short-lived badges are more secure. That's fine for a quick request, but some real operations in
this platform — an AI agent working through a complex task, a full grading run through two
separate judges, a complete deploy-and-grade cycle — can genuinely take longer than 5 minutes. A
call that fails partway through a long sequence with an error like:

```
{"error":{"code":"unauthorized","message":"invalid token: Signature has expired", ...}}
```

...is almost always this, not a real security problem. Check whether the badge being used was
issued more than about 5 minutes before the call that failed.

**How this is already handled in the platform's own code, and where it's bitten real development
work anyway:**

- The shared toolkit has a standard "try, and if it looks like an expired-badge problem, get a
  fresh one and try exactly once more" pattern, used throughout the platform — this is why most
  service-to-service calls don't fail this way even on a slow operation.
- A test or script that checks on a long-running operation repeatedly should get a *fresh* badge
  on every single check, not reuse the one it started with — found live, more than once, while
  proving this platform's own long-running flows actually work end to end.
- A multi-step process (build something, then publish it, then deploy it, then check on it)
  should fetch its badge fresh immediately before each individual step, not once at the very
  beginning — getting a badge too early and then waiting on a slow prior step can burn through
  most of its 5 minutes before the badge is ever actually used.

**If you hit this while debugging by hand,** just get a fresh badge:

```bash
TOKEN=$(curl -s -X POST http://localhost:8090/oauth/token \
  -d "grant_type=client_credentials&client_id=<id>&client_secret=<secret>&scope=<scope>" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")
```

**A badge carrying more permissions than a specific call actually needs is harmless**, not a
bug — a permission check only ever looks for the one specific permission it needs on that call,
so extra unused permissions on a badge don't cause problems. Still, when setting up a *new*
service, only grant the permissions it genuinely needs — that's just good practice, not something
enforced automatically.

## Temporal workflows and workers

Three parts of this platform use Temporal (a durable-workflow engine — think of it as a way to
reliably pause a process and wait for something, even across a restart): Approvals/HITL
(Layer 3), Deployment Pipeline (Layer 4), and anything that calls into either of those. All three
follow the exact same pattern:

- **Two separate running processes, not one.** An API process handles web requests and
  starts/checks on the durable process; a completely separate **worker** process is the one that
  actually carries out each step. Both are built from the same underlying code, just started
  differently.
- **A very simple, dependency-free file holds the shared data shapes.** Temporal re-loads this
  file under strict rules to guarantee its own "replay" safety mechanism works correctly; putting
  real application logic there has broken things before (a real, found-and-fixed bug), so it's
  kept deliberately bare.
- **Only the worker process actually does anything with side effects** (starting containers,
  calling other services, writing to the database). The durable process itself only decides what
  step comes next.

**A worker crashing mid-step does not lose the work** — Temporal itself keeps track of what a
durable process was doing independently of any one running worker, and everything resumes
correctly once a worker reconnects. This has been proven true in this platform, for real, not
just assumed. **However, one specific thing is still an open question, not yet fully
explained:** during that exact proof, Temporal's own safety-net timer (meant to catch a step
that's taking too long) fired much later than its own setting said it should — by roughly half an
hour, in this specific one-laptop development setup. Nothing was lost, and the eventual recovery
was correct — but *why* it took so much longer than configured isn't understood yet. **Don't
assume a configured timeout fires anywhere close to on time in this environment** — treat it as
a rough floor, not a guarantee, until this is better understood.

**If a durable process seems stuck,** check Temporal's own web dashboard
(`http://localhost:8081`) before assuming something is actually broken — it may simply be
waiting on a timer that hasn't fired yet, not genuinely hung.

## Secrets, credentials, and configuration

**No real secret ever goes into a saved file or a chat conversation.** Local development
settings are kept in a `.env` file that's deliberately excluded from the shared code repository;
a template of it (`.env.example`) is committed with placeholder values only. If a real API key or
password is ever accidentally shared somewhere it shouldn't be, treat it as compromised
immediately and replace it — don't just remove it from wherever it was pasted.

**Almost every password used between services in this platform is a fixed, publicly-known
development value, deliberately** — this is a local reference build, not a live deployed
system, so there's nothing sensitive to protect by randomizing them. Every new service that needs
its own login identity is registered with a fixed development password (recorded as a
scrambled/hashed value, following standard security practice, never in plain text) directly in
that service's own database setup script. Passing these fixed values into a running container
uses a simple "use this value, or fall back to this known default" pattern, so nothing extra
needs to be configured for the platform to just work locally.

**The one genuinely real secret pair in the whole platform** is the pair of keys the AI-model
gateway uses internally to talk to LiteLLM — generate your own local values for these, never
share or commit a real one (see `.env.example` for exactly how).

**Setting up a brand-new service that needs to call others:** copy an existing, recent
database-setup script from Agent Identity's own collection as your starting template. It's the
established, consistent way new services get registered and granted exactly the permissions they
need — nothing more.

## The debugging playbook

The single habit that found essentially every real problem across this entire platform's
construction: **look at what actually happened before guessing why.** Every issue documented in
`docs/decisions.md` was found this way, not by reasoning from the code alone. Work through these
in order:

**1. The failing service's own log output, first.**

```bash
docker compose logs -f <service>
docker logs -t <container>   # with timestamps, useful for comparing across services
```

An error from one service is often just a symptom — the real cause is frequently sitting in a
*different* service's own log, the one it was trying (and failing) to reach.

**2. The full request trace (Observability's dashboard, `http://localhost:16686`).** Every
request in this platform leaves a trace showing every service it touched, in order, with timing
for each step — the fastest way to find *which specific step* in a multi-service flow actually
failed or was slow.

**3. Checking a specific event channel directly,** when it's unclear whether something was
actually sent or received. This platform uses named channels (like a radio station) that services
publish events to and listen on; a small script can tune into any channel directly to see
everything published there recently, which is useful for confirming an expected event genuinely
happened.

**4. Manually fetching a badge and trying the call by hand** (see the Token TTLs section above
for the exact command), to isolate whether a failure is really about permissions before digging
into anything else.

**5. Directly inspecting Docker itself** (which network a container is actually on, what's
actually running inside it), specifically for the Docker-networking failure class described
above.

**Reproduce it, understand the real cause, then fix that specific thing — don't guess-and-patch.**
Every real problem documented in this platform's own history log was fixed at its actual root
cause and then re-checked using the exact same steps that first revealed it — never covered up
with a longer timeout, a blind retry, or code that silently swallows the error instead of
understanding it.
