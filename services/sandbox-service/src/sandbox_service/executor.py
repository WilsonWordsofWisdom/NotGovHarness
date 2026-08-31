"""Runs untrusted code in an ephemeral, network-isolated Docker container.

The `docker` SDK is synchronous; every call here runs in a worker thread
(`starlette.concurrency.run_in_threadpool`), same pattern as `platform_core.objectstore`'s MinIO
wrapper. Three properties this executor leans on were verified empirically against this repo's
actual Docker Engine before writing any of this (see D-050 and the harness design doc): a
`network_disabled` container's own network calls fail with a DNS resolution error, not merely an
app-level convention; `mem_limit` is enforced by the kernel's cgroup OOM killer *before* the
process's own allocator sees anything, and is observable afterward via the container's
`OOMKilled` state; `container.kill()` reliably stops a genuine infinite loop with no in-process
cooperation required.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import docker
import requests
from docker.errors import APIError, DockerException, ImageNotFound
from starlette.concurrency import run_in_threadpool

# Real host privilege, disclosed in D-050: this executor needs the Docker socket to launch
# containers. The isolation boundary this buys is executed-code-vs-host, not
# sandbox-service-vs-host.


@dataclass
class ExecutionResult:
    status: str  # "completed" | "oom_killed" | "timed_out" | "error"
    stdout: str
    stderr: str
    exit_code: int | None
    duration_ms: int


_LABEL_KEY = "com.notgovharness.sandbox"
_LABEL_VALUE = "true"


class Executor:
    def __init__(
        self,
        *,
        base_url: str | None,
        image: str,
        memory_limit: str,
        nano_cpus: int,
    ) -> None:
        self._client = docker.DockerClient(base_url=base_url) if base_url else docker.from_env()
        self._image = image
        self._memory_limit = memory_limit
        self._nano_cpus = nano_cpus

    async def ensure_image(self) -> None:
        """Pull the sandbox base image at startup, not on the first request — avoids a slow
        first execution the moment this service starts on a host that hasn't run it before."""
        await run_in_threadpool(self._client.images.pull, self._image)

    async def reap_orphaned_containers(self) -> int:
        """Remove any sandbox container left behind by a prior process that died mid-execution
        (this executor's own `finally: container.remove()` only runs if the process is alive to
        reach it — a `kill -9`/OOM of sandbox-service itself does not). Found live: killing this
        service mid-execution leaves the spawned container running indefinitely, since nothing
        else in this design supervises it. Every container this executor launches carries the
        `com.notgovharness.sandbox` label specifically so this reconciliation pass — run once at
        startup — can find and remove exactly those, and nothing else on the host's Docker
        Engine. Returns the number reaped."""
        return await run_in_threadpool(self._reap_orphaned_containers_sync)

    def _reap_orphaned_containers_sync(self) -> int:
        orphans = self._client.containers.list(
            all=True, filters={"label": f"{_LABEL_KEY}={_LABEL_VALUE}"}
        )
        for container in orphans:
            try:
                container.remove(force=True)
            except APIError:
                pass
        return len(orphans)

    async def run_python(self, code: str, *, timeout_seconds: int) -> ExecutionResult:
        return await run_in_threadpool(self._run_python_sync, code, timeout_seconds)

    def _run_python_sync(self, code: str, timeout_seconds: int) -> ExecutionResult:
        start = time.monotonic()
        try:
            container = self._client.containers.run(
                self._image,
                ["python3", "-c", code],
                detach=True,
                network_disabled=True,
                mem_limit=self._memory_limit,
                memswap_limit=self._memory_limit,  # equal to mem_limit disables swap entirely
                nano_cpus=self._nano_cpus,
                user="nobody",
                read_only=True,
                # python3 needs a writable /tmp for some stdlib operations even with -c; kept
                # tiny and separate from the (still read-only) rest of the filesystem.
                tmpfs={"/tmp": "size=16m"},
                security_opt=["no-new-privileges"],
                cap_drop=["ALL"],
                labels={_LABEL_KEY: _LABEL_VALUE},
            )
        except (ImageNotFound, APIError, DockerException) as exc:
            duration_ms = int((time.monotonic() - start) * 1000)
            return ExecutionResult(
                status="error", stdout="", stderr=str(exc), exit_code=None, duration_ms=duration_ms
            )

        try:
            try:
                result = container.wait(timeout=timeout_seconds)
                exit_code = result.get("StatusCode")
            except (requests.exceptions.ReadTimeout, requests.exceptions.ConnectionError):
                # The HTTP call to Docker timed out client-side; the container is still running
                # server-side until we explicitly kill it.
                try:
                    container.kill()
                except APIError:
                    pass  # already exited between the wait-timeout and this kill
                duration_ms = int((time.monotonic() - start) * 1000)
                return ExecutionResult(
                    status="timed_out",
                    stdout=self._logs(container, stdout=True),
                    stderr=self._logs(container, stdout=False),
                    exit_code=None,
                    duration_ms=duration_ms,
                )

            duration_ms = int((time.monotonic() - start) * 1000)
            container.reload()
            oom_killed = bool(container.attrs.get("State", {}).get("OOMKilled", False))
            status = "oom_killed" if oom_killed else "completed"
            return ExecutionResult(
                status=status,
                stdout=self._logs(container, stdout=True),
                stderr=self._logs(container, stdout=False),
                exit_code=exit_code,
                duration_ms=duration_ms,
            )
        finally:
            try:
                container.remove(force=True)
            except APIError:
                pass  # best-effort — a container that already vanished isn't a failure here

    @staticmethod
    def _logs(container, *, stdout: bool) -> str:
        try:
            return container.logs(stdout=stdout, stderr=not stdout).decode("utf-8", "replace")
        except APIError:
            return ""
