"""Create a minimal scripts repository (`toolrack init`). Click-free and non-destructive."""

from __future__ import annotations

import os

from toolrack import runtime

EXAMPLE_SCRIPT = '''\
import argparse

parser = argparse.ArgumentParser(description="Print a greeting.")
parser.add_argument("--name", required=True)
print(f"hello, {parser.parse_args().name}")
'''

EXAMPLE_SIDECAR = """\
description: Print a greeting.
args:
  - name: name
    required: true
    help: Name to greet.
"""

EXAMPLE_ENTRY = "scripts/example/hello.py"
CACHE_IGNORE_LINE = ".toolrack.cache.json"


def scaffold_repository(root: str, example: bool = True) -> list[tuple[str, str]]:
    """Create the repository layout under ``root``; never overwrite anything.

    Returns ``(relative_path, status)`` pairs where status is ``created``,
    ``updated`` or ``kept``.
    """
    root = os.path.abspath(root)
    registry_name = runtime.registry_basename()
    results: list[tuple[str, str]] = []

    def write_new(rel: str, content: str) -> None:
        path = os.path.join(root, rel)
        if os.path.exists(path):
            results.append((rel, "kept"))
            return
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
        results.append((rel, "created"))

    os.makedirs(os.path.join(root, "scripts"), exist_ok=True)

    registry_path = os.path.join(root, registry_name)
    if example:
        write_new(EXAMPLE_ENTRY, EXAMPLE_SCRIPT)
        write_new("scripts/example/hello.yml", EXAMPLE_SIDECAR)
    if not os.path.exists(registry_path):
        write_new(registry_name, (EXAMPLE_ENTRY + "\n") if example else "")
    else:
        existing = runtime.read_registry(_context(root))
        if example and EXAMPLE_ENTRY not in existing and os.path.isfile(os.path.join(root, EXAMPLE_ENTRY)):
            with open(registry_path, "a", encoding="utf-8", newline="\n") as handle:
                handle.write(EXAMPLE_ENTRY + "\n")
            results.append((registry_name, "updated"))
        else:
            results.append((registry_name, "kept"))

    gitignore = os.path.join(root, ".gitignore")
    if os.path.exists(gitignore):
        with open(gitignore, encoding="utf-8") as handle:
            text = handle.read()
        if CACHE_IGNORE_LINE not in text.splitlines():
            with open(gitignore, "a", encoding="utf-8", newline="\n") as handle:
                handle.write(("" if text.endswith("\n") or not text else "\n") + CACHE_IGNORE_LINE + "\n")
            results.append((".gitignore", "updated"))
        else:
            results.append((".gitignore", "kept"))
    else:
        write_new(".gitignore", CACHE_IGNORE_LINE + "\n")
    return results


def _context(root: str) -> runtime.RepositoryContext:
    registry = os.path.join(root, runtime.registry_basename())
    return runtime.RepositoryContext(
        repo_root=root,
        scripts_root=os.path.join(root, "scripts"),
        registry_file=registry,
        cache_file=registry + ".cache.json",
        aliases_file=os.path.join(root, "aliases.cfg"),
    )


def repository_context(root: str) -> runtime.RepositoryContext:
    return _context(os.path.abspath(root))
