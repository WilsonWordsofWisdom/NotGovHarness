#!/bin/sh
# One-shot bootstrap: generate the agent's join token and register workload entries.
#
# Runs `docker exec` into the (already-running, healthy) spire-server container rather than
# calling the spire-server binary locally — the upstream spire-server image ships no shell, only
# the Go binary, so this container (docker:cli — has both a shell and the docker CLI) drives it
# from outside instead. -socketPath is a path inside spire-server's own filesystem (server.conf's
# socket_path), not this container's, since `docker exec` runs literally inside that container.
set -eu

SOCK="/run/spire/server/private/api.sock"

echo "Generating a join token for the agent..."
# grep first: `token generate` without -spiffeID also prints a "Warning: Missing SPIFFE ID."
# line to stdout, which corrupted TOKEN (embedded newline + text) when only sed filtered it.
TOKEN=$(docker exec spire-server /opt/spire/bin/spire-server token generate \
  -socketPath "$SOCK" \
  | grep '^Token:' | sed 's/^Token: //')
echo -n "$TOKEN" > /run/spire/bootstrap/join_token

# join_token node attestation doesn't honor an explicit -spiffeID — the resulting node's SPIFFE
# ID is always spire/agent/join_token/<the token itself> (verified against a real agent's attest
# log: the UUID in the logged spiffe_id matched the generated token exactly). Entries must use
# *that* as parentID, or the agent will never be handed them.
AGENT_ID="spiffe://notgovharness/spire/agent/join_token/$TOKEN"

echo "Registering workload entries..."
# Idempotent across restarts: `entry create` errors on an exact duplicate, which is fine — the
# entry is already there, and there's nothing else in this script that a partial re-run breaks.
# (Each re-run mints a fresh agent ID, though, since the token changes — see infra/spire/README.)
docker exec spire-server /opt/spire/bin/spire-server entry create \
  -socketPath "$SOCK" \
  -parentID "$AGENT_ID" \
  -spiffeID spiffe://notgovharness/example-service \
  -selector docker:label:com.docker.compose.service:example-service \
  || true

docker exec spire-server /opt/spire/bin/spire-server entry create \
  -socketPath "$SOCK" \
  -parentID "$AGENT_ID" \
  -spiffeID spiffe://notgovharness/upstream-stub \
  -selector docker:label:com.docker.compose.service:upstream-stub \
  || true

echo "spire-bootstrap done."
