"""Tests for language_analysis.py — pure, no external services required."""

import os
import tempfile

import pytest

from codebase_insights.language_analysis import (
    Language,
    detect_language,
    detect_languages,
    extension_language_map,
    _parse_gitignore,
)


# ---------------------------------------------------------------------------
# Language enum & extension map
# ---------------------------------------------------------------------------

class TestLanguageEnum:
    def test_members_exist(self):
        assert Language.PYTHON.value == "python"
        assert Language.JS_TS.value == "javascript/typescript"
        assert Language.CPP.value == "cpp"
        assert Language.RUST.value == "rust"

    def test_extension_map_covers_common_extensions(self):
        for ext in (".py", ".js", ".ts", ".tsx", ".cpp", ".h", ".rs"):
            assert ext in extension_language_map, f"{ext} missing from extension_language_map"


# ---------------------------------------------------------------------------
# detect_language
# ---------------------------------------------------------------------------

class TestDetectLanguage:
    @pytest.mark.parametrize("filename,expected", [
        ("main.py",        Language.PYTHON),
        ("app.js",         Language.JS_TS),
        ("component.tsx",  Language.JS_TS),
        ("server.ts",      Language.JS_TS),
        ("main.cpp",       Language.CPP),
        ("header.hpp",     Language.CPP),
        ("lib.rs",         Language.RUST),
    ])
    def test_known_extensions(self, filename, expected):
        assert detect_language(filename) == expected

    def test_unknown_extension_returns_none(self):
        assert detect_language("README.md") is None
        assert detect_language("Makefile") is None
        assert detect_language("data.json") is None

    def test_case_sensitivity(self):
        # Python's os.path.splitext is case-preserving; ".PY" != ".py"
        result = detect_language("MAIN.PY")
        assert result is None  # extension map uses lowercase keys


# ---------------------------------------------------------------------------
# _parse_gitignore
# ---------------------------------------------------------------------------

class TestParseGitignore:
    def _make_gitignore(self, tmp_dir: str, content: str) -> str:
        path = os.path.join(tmp_dir, ".gitignore")
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return path

    def test_name_only_pattern_added_to_ignored_names(self, tmp_path):
        path = self._make_gitignore(str(tmp_path), "node_modules\n__pycache__\n")
        ignored_paths: set = set()
        ignored_names: set = set()
        _parse_gitignore(path, str(tmp_path), ignored_paths, ignored_names)
        assert "node_modules" in ignored_names
        assert "__pycache__" in ignored_names

    def test_root_relative_pattern_added_to_ignored_paths(self, tmp_path):
        path = self._make_gitignore(str(tmp_path), "/dist\n")
        ignored_paths: set = set()
        ignored_names: set = set()
        _parse_gitignore(path, str(tmp_path), ignored_paths, ignored_names)
        expected = os.path.normpath(os.path.join(str(tmp_path), "dist"))
        assert expected in ignored_paths

    def test_blank_lines_and_comments_skipped(self, tmp_path):
        path = self._make_gitignore(str(tmp_path), "# comment\n\nbuild\n")
        ignored_paths: set = set()
        ignored_names: set = set()
        _parse_gitignore(path, str(tmp_path), ignored_paths, ignored_names)
        assert "build" in ignored_names
        assert len(ignored_paths) == 0

    def test_trailing_slash_stripped(self, tmp_path):
        path = self._make_gitignore(str(tmp_path), "dist/\n")
        ignored_paths: set = set()
        ignored_names: set = set()
        _parse_gitignore(path, str(tmp_path), ignored_paths, ignored_names)
        # "dist/" has no leading slash and no internal slash after stripping → name pattern
        assert "dist" in ignored_names


# ---------------------------------------------------------------------------
# detect_languages (integration — uses a real temp directory)
# ---------------------------------------------------------------------------

class TestDetectLanguages:
    def test_detects_python_only(self, tmp_path):
        (tmp_path / "main.py").write_text("print('hello')")
        (tmp_path / "README.md").write_text("# readme")
        result = detect_languages(str(tmp_path))
        assert result == {Language.PYTHON}

    def test_detects_multiple_languages(self, tmp_path):
        (tmp_path / "app.py").write_text("")
        (tmp_path / "app.ts").write_text("")
        (tmp_path / "lib.rs").write_text("")
        result = detect_languages(str(tmp_path))
        assert Language.PYTHON in result
        assert Language.JS_TS in result
        assert Language.RUST in result

    def test_empty_directory_returns_empty_set(self, tmp_path):
        result = detect_languages(str(tmp_path))
        assert result == set()

    def test_gitignored_directory_excluded(self, tmp_path):
        ignored_dir = tmp_path / "node_modules"
        ignored_dir.mkdir()
        (ignored_dir / "index.js").write_text("")
        (tmp_path / ".gitignore").write_text("node_modules\n")
        # Only non-ignored files
        (tmp_path / "main.py").write_text("")
        result = detect_languages(str(tmp_path))
        # JS should NOT appear because node_modules is gitignored
        assert Language.JS_TS not in result
        assert Language.PYTHON in result
