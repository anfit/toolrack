"""Windows / Git Bash / Cygwin path and interpreter handling."""

from __future__ import annotations

import os
import subprocess


def is_windows() -> bool:
    return os.name == "nt"


def to_bash_path(windows_path: str, bash_exe: str = "") -> str:
    # TODO(#5): Isolate shell/path translation behind a shell adapter layer.
    # Windows, Git Bash, and Cygwin compatibility logic is mixed into the core
    # dispatcher, which makes the module harder to reason about than it should be.
    if not is_windows():
        return windows_path

    cygpath_candidates: list[str] = []
    if bash_exe:
        bash_dir = os.path.dirname(os.path.abspath(bash_exe))
        same_dir = os.path.join(bash_dir, "cygpath.exe")
        parent_usr_bin = os.path.join(os.path.dirname(bash_dir), "usr", "bin", "cygpath.exe")
        for candidate in (same_dir, parent_usr_bin):
            if os.path.isfile(candidate):
                cygpath_candidates.append(candidate)
    cygpath_candidates.append("cygpath")

    for cygpath_exe in cygpath_candidates:
        try:
            result = subprocess.run(
                [cygpath_exe, "-u", windows_path],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                return result.stdout.strip()
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue

    path = windows_path.replace("\\", "/")
    if len(path) >= 2 and path[1] == ":":
        path = "/" + path[0].lower() + path[2:]
    return path


def resolve_bash_exe() -> str:
    if not is_windows():
        return "bash"

    cygwin_bash = r"C:\cygwin64\bin\bash.exe"
    git_bash = r"C:\Program Files\Git\bin\bash.exe"

    if not os.environ.get("MSYSTEM"):
        path_dirs = os.environ.get("PATH", "").split(os.pathsep)
        cygwin_bin = r"C:\cygwin64\bin"
        gitbash_bin = r"C:\Program Files\Git\bin"

        def _norm(value: str) -> str:
            return os.path.normcase(os.path.normpath(value))

        normed = [_norm(entry) for entry in path_dirs]
        cygwin_idx = next((idx for idx, entry in enumerate(normed) if entry == _norm(cygwin_bin)), 999)
        gitbash_idx = next((idx for idx, entry in enumerate(normed) if entry == _norm(gitbash_bin)), 999)
        if cygwin_idx < gitbash_idx and os.path.isfile(cygwin_bash):
            return cygwin_bash

    if os.environ.get("MSYSTEM") and os.path.isfile(git_bash):
        return git_bash

    return "bash"
