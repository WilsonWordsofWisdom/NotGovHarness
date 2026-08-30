"""Extracts and sanity-checks an uploaded skill bundle (zip archive) before storage.

The Agent Skills standard has no signing concept (D-035), but a reference platform accepting
arbitrary uploaded archives still shouldn't accept a zip bomb or a path-traversal entry — a size
cap and a reject-any-unsafe-entry check, basic hygiene rather than a scope expansion.
"""

from __future__ import annotations

import zipfile
from dataclasses import dataclass
from io import BytesIO

MAX_BUNDLE_BYTES = 10 * 1024 * 1024  # 10 MiB, uncompressed total
MAX_ENTRY_COUNT = 500


class BundleError(Exception):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


@dataclass
class ExtractedBundle:
    directory_name: str
    skill_md_content: str


def extract_skill_md(data: bytes) -> ExtractedBundle:
    """Raises ``BundleError`` unless ``data`` is a safe zip archive of a single skill directory
    containing ``<directory>/SKILL.md``.
    """
    if len(data) > MAX_BUNDLE_BYTES:
        raise BundleError(f"bundle exceeds max upload size of {MAX_BUNDLE_BYTES} bytes")

    try:
        archive = zipfile.ZipFile(BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise BundleError(f"not a valid zip archive: {exc}") from exc

    infos = archive.infolist()
    if len(infos) > MAX_ENTRY_COUNT:
        raise BundleError(f"bundle has more than {MAX_ENTRY_COUNT} entries")

    total_uncompressed = 0
    top_level_dirs: set[str] = set()
    skill_md_info: zipfile.ZipInfo | None = None

    for info in infos:
        name = info.filename
        if name.startswith("/") or ".." in name.split("/"):
            raise BundleError(f"unsafe path in archive entry: {name!r}")

        total_uncompressed += info.file_size
        if total_uncompressed > MAX_BUNDLE_BYTES:
            raise BundleError(f"bundle exceeds max uncompressed size of {MAX_BUNDLE_BYTES} bytes")

        parts = name.split("/")
        if parts[0]:
            top_level_dirs.add(parts[0])
        if len(parts) == 2 and parts[1] == "SKILL.md":
            skill_md_info = info

    if len(top_level_dirs) != 1:
        raise BundleError(
            f"bundle must contain exactly one top-level directory (found {sorted(top_level_dirs)})"
        )
    if skill_md_info is None:
        raise BundleError("bundle must contain <directory>/SKILL.md")

    directory_name = next(iter(top_level_dirs))
    skill_md_content = archive.read(skill_md_info).decode("utf-8")
    return ExtractedBundle(directory_name=directory_name, skill_md_content=skill_md_content)
