"""Tests for workspace_indexer pure utilities — no LSP or DB required."""

import os
import re

import pytest

from codebase_insights.workspace_indexer import (
    _ANON_NAME_RE,
    _path_to_uri,
    _uri_to_path,
    canonical_path,
)


# ---------------------------------------------------------------------------
# canonical_path
# ---------------------------------------------------------------------------

class TestCanonicalPath:
    def test_plain_path_is_normalised(self, tmp_path):
        p = str(tmp_path / "a" / "b.py")
        assert canonical_path(p) == os.path.normpath(p)

    def test_file_uri_with_three_slashes(self, tmp_path):
        raw = str(tmp_path / "file.py").replace("\\", "/")
        # Build URI manually so it works on both platforms
        if os.name == "nt":
            uri = f"file:///{raw}"
        else:
            uri = f"file://{raw}"
        result = canonical_path(uri)
        assert result == canonical_path(str(tmp_path / "file.py"))

    def test_forward_and_back_slashes_equivalent(self, tmp_path):
        base = str(tmp_path)
        p1 = base.replace("\\", "/") + "/sub/file.py"
        p2 = base.replace("/", "\\") + "\\sub\\file.py"
        # Both should normalise to the same path
        assert canonical_path(p1) == canonical_path(p2)

    @pytest.mark.skipif(os.name != "nt", reason="Windows-only: drive letter normalisation")
    def test_drive_letter_uppercased_on_windows(self):
        result = canonical_path("c:\\Users\\test\\file.py")
        assert result[0] == "C"


# ---------------------------------------------------------------------------
# _path_to_uri / _uri_to_path round-trip
# ---------------------------------------------------------------------------

class TestUriRoundTrip:
    def test_round_trip(self, tmp_path):
        path = str(tmp_path / "src" / "main.py")
        uri = _path_to_uri(path)
        assert uri.startswith("file:///") or uri.startswith("file://")
        recovered = _uri_to_path(uri)
        assert recovered == canonical_path(path)


# ---------------------------------------------------------------------------
# _ANON_NAME_RE — anonymous symbol filter
# ---------------------------------------------------------------------------

class TestAnonNameRe:
    @pytest.mark.parametrize("name", [
        "$",
        "$0",
        "_",
        "__",
        "_1",
        "<anonymous>",
        "<lambda>",
        "anonymous",
        "42",
        "0",
        "callback",
        "handler",
        "wrapper",
        "on click callback",
        ".then",
        ".map",
    ])
    def test_anon_names_match(self, name):
        assert _ANON_NAME_RE.search(name), f"Expected {name!r} to be matched as anonymous"

    @pytest.mark.parametrize("name", [
        "MyClass",
        "process_data",
        "WorkspaceIndexer",
        "canonical_path",
        "detect_language",
        "Language",
    ])
    def test_real_names_do_not_match(self, name):
        assert not _ANON_NAME_RE.search(name), f"Expected {name!r} NOT to be matched as anonymous"
