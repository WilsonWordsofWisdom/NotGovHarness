"""Executor tests run against the real Docker Engine, not a mock — a mock would assert this
design's intent, not that --network none / --memory / container.kill() actually do what D-050's
design doc claims on this host. No live-stack dependency (no Postgres/identity-service): these
only need Docker, so they're not gated behind a skip-if-down fixture.
"""

from __future__ import annotations

import docker
import pytest
from docker.errors import DockerException, NotFound
from sandbox_service.executor import Executor


def _docker_available() -> bool:
    try:
        docker.from_env().ping()
        return True
    except DockerException:
        return False


pytestmark = pytest.mark.skipif(not _docker_available(), reason="Docker Engine not reachable")


@pytest.fixture
def executor() -> Executor:
    return Executor(
        base_url=None, image="python:3.12-slim", memory_limit="128m", nano_cpus=500_000_000
    )


async def test_normal_execution_returns_stdout(executor: Executor):
    result = await executor.run_python("print(1 + 1)", timeout_seconds=10)
    assert result.status == "completed"
    assert result.stdout == "2\n"
    assert result.exit_code == 0


async def test_nonzero_exit_is_still_completed_not_error(executor: Executor):
    result = await executor.run_python("import sys; sys.exit(3)", timeout_seconds=10)
    assert result.status == "completed"
    assert result.exit_code == 3


async def test_network_is_genuinely_blocked(executor: Executor):
    code = """
import urllib.request
try:
    urllib.request.urlopen("http://example.com", timeout=3)
    print("LEAK")
except Exception as e:
    print("BLOCKED:", type(e).__name__)
"""
    result = await executor.run_python(code, timeout_seconds=10)
    assert result.status == "completed"
    assert "BLOCKED" in result.stdout
    assert "LEAK" not in result.stdout


async def test_memory_bomb_is_oom_killed(executor: Executor):
    code = """
data = []
while True:
    data.append(bytearray(10**7))
"""
    result = await executor.run_python(code, timeout_seconds=10)
    assert result.status == "oom_killed"
    assert result.exit_code == 137


async def test_infinite_loop_is_killed_at_timeout(executor: Executor):
    result = await executor.run_python("while True: pass", timeout_seconds=2)
    assert result.status == "timed_out"
    assert result.exit_code is None
    # Killed close to the requested timeout, not left running well past it.
    assert result.duration_ms < 8_000


async def test_reap_orphaned_containers_removes_leftover_labeled_containers(executor: Executor):
    # Simulates what a `kill -9` of sandbox-service mid-execution leaves behind: a still-running
    # sandbox container nothing is supervising anymore (found live during this harness's own
    # verification — see the reap_orphaned_containers docstring).
    client = docker.from_env()
    orphan = client.containers.run(
        "python:3.12-slim",
        ["sleep", "60"],
        detach=True,
        network_disabled=True,
        labels={"com.notgovharness.sandbox": "true"},
    )
    try:
        reaped = await executor.reap_orphaned_containers()
        assert reaped >= 1
        orphan.reload()
    except NotFound:
        pass  # removed, as intended
    else:
        raise AssertionError("orphaned container was not reaped")


async def test_containers_are_cleaned_up_after_every_outcome(executor: Executor):
    before = len(
        docker.from_env().containers.list(all=True, filters={"ancestor": "python:3.12-slim"})
    )

    await executor.run_python("print('ok')", timeout_seconds=10)
    await executor.run_python("while True: pass", timeout_seconds=2)
    await executor.run_python(
        "data=[]\nwhile True: data.append(bytearray(10**7))", timeout_seconds=10
    )

    after = len(
        docker.from_env().containers.list(all=True, filters={"ancestor": "python:3.12-slim"})
    )
    assert after == before
