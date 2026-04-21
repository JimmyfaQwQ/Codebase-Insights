import os
import enum
import time

from . import cli_io


class Language(enum.Enum):
    PYTHON = "python"
    JS_TS = "javascript/typescript"
    CPP = "cpp"
    RUST = "rust"

extension_language_map = {
    ".py": Language.PYTHON,
    ".js": Language.JS_TS,
    ".jsx": Language.JS_TS,
    ".ts": Language.JS_TS,
    ".tsx": Language.JS_TS,
    ".cpp": Language.CPP,
    ".h": Language.CPP,
    ".hpp": Language.CPP,
    ".c": Language.CPP,
    ".m": Language.CPP,
    ".mm": Language.CPP,
    ".rs": Language.RUST,
}


def detect_languages(root_dir: str) -> set[Language]:
    """Detect programming languages used in the codebase by scanning file extensions."""
    detected_languages = set()
    decend_into_directory(root_dir, detected_languages)
    
    return detected_languages

def _parse_gitignore(gitignore_path: str, base_dir: str, ignored_paths: set, ignored_names: set):
    with open(gitignore_path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            pattern = line.rstrip("/")
            if pattern.startswith("/"):
                # Root-relative: resolve against the .gitignore's directory
                ignored_paths.add(os.path.normpath(os.path.join(base_dir, pattern.lstrip("/"))))
            elif "/" not in pattern:
                # No slash: matches any entry with this name anywhere in the tree
                ignored_names.add(pattern)
            else:
                # Relative path pattern
                ignored_paths.add(os.path.normpath(os.path.join(base_dir, pattern)))


def decend_into_directory(root_dir: str, detected_languages: set[Language]):
    """Recursively scan directories for files matching known language extensions."""
    ignored_paths: set[str] = set()  # exact normalized paths to ignore
    ignored_names: set[str] = set()  # name-only patterns that match anywhere in the tree

    n = 0
    t_start = time.monotonic()
    t_last  = 0.0

    bar = cli_io.tqdm(total=None, desc="[Status] Scanning workspace", unit="file", section="status")

    def _print_progress():
        nonlocal t_last
        now = time.monotonic()
        if now - t_last < 0.1:
            return
        t_last = now
        elapsed = now - t_start
        rate = n / elapsed if elapsed > 0 else 0
        bar.set_postfix(rate=f"{rate:.1f}/s", elapsed=f"{elapsed:.1f}s")

    # Single pass: parse .gitignore, prune, and scan files together
    for dirpath, dirnames, filenames in os.walk(root_dir, topdown=True):
        if ".gitignore" in filenames:
            _parse_gitignore(os.path.join(dirpath, ".gitignore"), dirpath, ignored_paths, ignored_names)
        dirnames[:] = [
            d for d in dirnames
            if os.path.normpath(os.path.join(dirpath, d)) not in ignored_paths
            and d not in ignored_names
        ]
        for filename in filenames:
            file_path = os.path.join(dirpath, filename)
            if os.path.normpath(file_path) in ignored_paths or filename in ignored_names:
                continue
            lang = detect_language(file_path)
            if lang is not None:
                detected_languages.add(lang)
            n += 1
            bar.update(1)
            _print_progress()
    bar.close()


def detect_language(file_path: str) -> Language | None:
    """Detect the programming language of a file based on its extension."""
    _, ext = os.path.splitext(file_path)
    return extension_language_map.get(ext)
