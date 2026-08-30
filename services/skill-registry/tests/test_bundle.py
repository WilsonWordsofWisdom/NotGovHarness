"""Infra-free: extract_bundle against in-memory zip archives — no Postgres/MinIO needed to
prove the extraction and safety checks.
"""

from __future__ import annotations

import zipfile
from io import BytesIO

import pytest
from skill_registry.bundle import MAX_BUNDLE_BYTES, BundleError, extract_bundle


def _zip(entries: dict[str, bytes]) -> bytes:
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    return buf.getvalue()


def test_valid_bundle_extracts_directory_name_skill_md_and_all_files():
    data = _zip(
        {
            "widget-skill/SKILL.md": b"---\nname: widget-skill\ndescription: d\n---\nbody",
            "widget-skill/scripts/run.py": b"print('hi')",
            "widget-skill/references/REFERENCE.md": b"# ref",
        }
    )
    extracted = extract_bundle(data)
    assert extracted.directory_name == "widget-skill"
    assert "name: widget-skill" in extracted.skill_md_content
    assert set(extracted.files) == {"SKILL.md", "scripts/run.py", "references/REFERENCE.md"}
    assert extracted.files["scripts/run.py"] == b"print('hi')"


def test_not_a_zip_file_is_rejected():
    with pytest.raises(BundleError, match="not a valid zip"):
        extract_bundle(b"this is definitely not a zip archive")


def test_path_traversal_entry_is_rejected():
    data = _zip({"widget-skill/SKILL.md": b"x", "widget-skill/../evil.txt": b"y"})
    with pytest.raises(BundleError, match="unsafe path"):
        extract_bundle(data)


def test_absolute_path_entry_is_rejected():
    data = _zip({"widget-skill/SKILL.md": b"x", "/etc/passwd": b"y"})
    with pytest.raises(BundleError, match="unsafe path"):
        extract_bundle(data)


def test_multiple_top_level_directories_is_rejected():
    data = _zip({"skill-a/SKILL.md": b"x", "skill-b/SKILL.md": b"y"})
    with pytest.raises(BundleError, match="top-level directory"):
        extract_bundle(data)


def test_missing_skill_md_is_rejected():
    data = _zip({"widget-skill/scripts/run.py": b"print('hi')"})
    with pytest.raises(BundleError, match="SKILL.md"):
        extract_bundle(data)


def test_non_utf8_skill_md_is_rejected():
    data = _zip({"widget-skill/SKILL.md": b"\xff\xfe not valid utf-8"})
    with pytest.raises(BundleError, match="UTF-8"):
        extract_bundle(data)


def test_oversized_upload_is_rejected():
    with pytest.raises(BundleError, match="max upload size"):
        extract_bundle(b"0" * (MAX_BUNDLE_BYTES + 1))


def test_oversized_uncompressed_content_is_rejected():
    # A small *compressed* payload (highly-compressible zeros, DEFLATE) that expands past the
    # cap once uncompressed — the upload itself stays under MAX_BUNDLE_BYTES, so this exercises
    # the uncompressed-size check specifically, not the outer upload-size cap.
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("widget-skill/SKILL.md", b"0" * (MAX_BUNDLE_BYTES + 1))
    data = buf.getvalue()
    assert len(data) < MAX_BUNDLE_BYTES
    with pytest.raises(BundleError, match="uncompressed size"):
        extract_bundle(data)
