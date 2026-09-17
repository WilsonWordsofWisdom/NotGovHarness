"""Fetches a suite's metadata + dataset from `eval-registry`. Both are open GET endpoints — no
scope required, per eval-registry's own design.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import httpx


class EvalRegistryError(Exception):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


@dataclass
class Golden:
    input: str
    expected_output: str | None
    context: list[str] | None
    expected_tools: list[str] | None
    metadata: dict[str, Any]


@dataclass
class SuiteAndDataset:
    name: str
    version: str
    kind: str
    metrics: list[dict[str, Any]] | None
    goldens: list[Golden]


class EvalRegistryClient:
    def __init__(self, base_url: str) -> None:
        self._base_url = base_url

    async def fetch(self, name: str, version: str | None = None) -> SuiteAndDataset:
        async with httpx.AsyncClient(base_url=self._base_url, timeout=15.0) as client:
            suite_path = f"/suites/{name}" if version is None else f"/suites/{name}/{version}"
            resp = await client.get(suite_path)
            if resp.status_code == 404:
                raise EvalRegistryError(f"no suite published for {name!r} version {version!r}")
            resp.raise_for_status()
            suite = resp.json()

            if suite["kind"] != "cases":
                raise EvalRegistryError(
                    f"suite {name!r} version {suite['version']!r} has kind "
                    f"{suite['kind']!r} — only 'cases' suites are runnable (v1)"
                )

            dataset_resp = await client.get(
                f"/suites/{suite['name']}/{suite['version']}/dataset"
            )
            dataset_resp.raise_for_status()
            goldens = _parse_goldens(dataset_resp.text)

        return SuiteAndDataset(
            name=suite["name"],
            version=suite["version"],
            kind=suite["kind"],
            metrics=suite["metrics"],
            goldens=goldens,
        )


def _parse_goldens(jsonl_text: str) -> list[Golden]:
    goldens: list[Golden] = []
    for line in jsonl_text.splitlines():
        line = line.strip()
        if not line:
            continue
        obj = json.loads(line)
        goldens.append(
            Golden(
                input=obj["input"],
                expected_output=obj.get("expected_output"),
                context=obj.get("context"),
                expected_tools=obj.get("expected_tools"),
                metadata=obj.get("metadata", {}),
            )
        )
    return goldens
