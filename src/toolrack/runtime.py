"""Click-free runtime primitives shared by the CLI and the completion fast path.

Everything here must stay importable without Click or PyYAML: the shell
completion fast path (``toolrack.completion``) imports this module on every
<TAB> press, and the CLI imports it so both sides agree on how the repository
is located and how the command-metadata cache is validated.
"""

from __future__ import annotations

import json
import ntpath
import os
import sys
from collections import namedtuple
from collections.abc import Mapping

LEGACY_REGISTRY_BASENAME = ".attic"
DEFAULT_REGISTRY_BASENAME = ".toolrack"


# A namedtuple rather than a dataclass: importing `dataclasses` costs more than
# the rest of this module put together, and this runs on every <TAB>.
RepositoryContext = namedtuple(
    "RepositoryContext",
    ["repo_root", "scripts_root", "registry_file", "cache_file", "aliases_file"],
)


def default_cli_name() -> str:
    prog = os.path.basename(sys.argv[0] or "").strip()
    if prog:
        prog = os.path.splitext(prog)[0]
        if prog not in {"", "__main__", "-m"}:
            return prog
    return "toolrack"


def cli_name(environ: Mapping[str, str] = os.environ) -> str:
    return environ.get("TOOLRACK_CLI_NAME") or default_cli_name()


def completion_var(name: str) -> str:
    return "_" + name.replace("-", "_").upper() + "_COMPLETE"


def registry_basename(environ: Mapping[str, str] = os.environ) -> str:
    return environ.get("TOOLRACK_REGISTRY_BASENAME") or DEFAULT_REGISTRY_BASENAME


def is_windows() -> bool:
    return os.name == "nt"


def normalize_env_path(value: str | None) -> str | None:
    """Map Git Bash / Cygwin Windows mount paths back to native Windows paths."""
    if not value or not is_windows():
        return value

    normalized = value.replace("\\", "/")
    if normalized.startswith("/cygdrive/") and len(normalized) > len("/cygdrive/x/"):
        drive = normalized[len("/cygdrive/")]
        remainder = normalized[len("/cygdrive/x") :]
        return ntpath.normpath(f"{drive.upper()}:{remainder}")

    if normalized.startswith("/") and len(normalized) > 3 and normalized[2] == "/":
        drive = normalized[1]
        if drive.isalpha():
            return ntpath.normpath(f"{drive.upper()}:{normalized[2:]}")

    return value


def find_repo_root(environ: Mapping[str, str] = os.environ) -> str:
    """Walk up from cwd until we find the registry file or .git."""
    candidates = [
        environ.get("TOOLRACK_REGISTRY_FILE"),
        registry_basename(environ),
        LEGACY_REGISTRY_BASENAME,
    ]
    names = [name for name in candidates if name and not os.path.isabs(name)]

    here = os.path.abspath(os.getcwd())
    candidate = here
    while True:
        if any(os.path.isfile(os.path.join(candidate, name)) for name in names):
            return candidate
        if os.path.isdir(os.path.join(candidate, ".git")):
            return candidate
        parent = os.path.dirname(candidate)
        if parent == candidate:
            return here
        candidate = parent


def resolve_repository(environ: Mapping[str, str] = os.environ) -> RepositoryContext:
    """Resolve repository locations from the TOOLRACK_* environment."""

    def path_from_env(name: str) -> str | None:
        return normalize_env_path(environ.get(name))

    repo_root = path_from_env("TOOLRACK_REPO_ROOT") or find_repo_root(environ)
    registry_file = path_from_env("TOOLRACK_REGISTRY_FILE") or os.path.join(
        repo_root, registry_basename(environ)
    )
    return RepositoryContext(
        repo_root=repo_root,
        scripts_root=path_from_env("TOOLRACK_SCRIPTS_ROOT") or os.path.join(repo_root, "scripts"),
        registry_file=registry_file,
        cache_file=path_from_env("TOOLRACK_CACHE_FILE") or registry_file + ".cache.json",
        aliases_file=path_from_env("TOOLRACK_ALIASES_FILE")
        or os.path.join(repo_root, "aliases.cfg"),
    )


def read_registry(repo: RepositoryContext) -> list[str]:
    if not os.path.isfile(repo.registry_file):
        return []
    with open(repo.registry_file, encoding="utf-8") as handle:
        return [
            line.strip()
            for line in handle
            if line.strip() and not line.strip().startswith("#")
        ]


def abs_script_path(registry_entry: str, repo: RepositoryContext) -> str:
    return os.path.normpath(os.path.join(repo.repo_root, registry_entry))


def sidecar_path(script_path: str) -> str:
    return os.path.splitext(script_path)[0] + ".yml"


def file_state(path: str) -> dict[str, int | bool]:
    try:
        stat = os.stat(path)
    except FileNotFoundError:
        return {"exists": False, "size": 0, "mtime_ns": 0}
    return {"exists": True, "size": stat.st_size, "mtime_ns": stat.st_mtime_ns}


def cache_signature(registry: list[str], repo: RepositoryContext) -> str:
    payload = {
        "repo_root": repo.repo_root,
        "scripts_root": repo.scripts_root,
        "registry_file": repo.registry_file,
        "aliases_file": repo.aliases_file,
        "registry": registry,
        "registry_state": file_state(repo.registry_file),
        "aliases_state": file_state(repo.aliases_file),
        "entries": [
            {
                "registry_entry": entry,
                "script_state": file_state(abs_script_path(entry, repo)),
                "sidecar_state": file_state(sidecar_path(abs_script_path(entry, repo))),
            }
            for entry in registry
        ],
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def read_cache(registry: list[str], repo: RepositoryContext) -> list[dict] | None:
    """Return cached command entries, or None when missing or stale."""
    if not os.path.isfile(repo.cache_file):
        return None
    try:
        with open(repo.cache_file, encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or payload.get("signature") != cache_signature(
        registry, repo
    ):
        return None
    entries = payload.get("entries")
    return entries if isinstance(entries, list) else None
