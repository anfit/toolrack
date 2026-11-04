"""Environment and installation diagnostics (`toolrack doctor`).

Complements `core validate`, which checks the repository's contents. Doctor
asks "is toolrack itself set up sanely on this machine?". Click-free; every
probe is a small function so it can be tested with injected inputs.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from typing import Callable, NamedTuple

from toolrack import __version__, runtime

OK, WARN, FAIL = "ok", "warn", "fail"


class Check(NamedTuple):
    level: str
    name: str
    detail: str


def _install_description() -> str:
    """How this copy of toolrack was installed (editable checkout vs normal)."""
    from importlib import metadata

    location = os.path.dirname(os.path.abspath(__file__))
    try:
        dist = metadata.distribution("toolrack-cli")
        raw = dist.read_text("direct_url.json")
        if raw and json.loads(raw).get("dir_info", {}).get("editable"):
            return f"{__version__}, editable install from {location}"
    except (metadata.PackageNotFoundError, ValueError):
        pass
    return f"{__version__} at {location}"


def legacy_distribution() -> str | None:
    """Version of a separately installed ``toolrack`` distribution, if any.

    Before the PyPI release the engine was installed as ``toolrack``. Any
    distribution of that name ships the same import package as ``toolrack-cli``
    and can shadow it.
    """
    from importlib import metadata

    try:
        return metadata.version("toolrack")
    except metadata.PackageNotFoundError:
        return None


def run_checks(
    repo: runtime.RepositoryContext,
    cli_name: str,
    check_repository: Callable[[runtime.RepositoryContext], tuple[list[str], list]],
    registry_entries: list[str] | None = None,
) -> list[Check]:
    checks: list[Check] = []
    add = lambda level, name, detail: checks.append(Check(level, name, detail))  # noqa: E731

    add(OK, "toolrack", _install_description())
    add(OK, "python", f"{sys.version.split()[0]} at {sys.executable}")

    legacy = legacy_distribution()
    if legacy:
        add(
            WARN,
            "legacy install",
            f"a separate 'toolrack' distribution ({legacy}) is installed and can shadow toolrack-cli; "
            "run: pip uninstall toolrack",
        )

    if not os.path.isfile(repo.registry_file):
        add(FAIL, "repository", f"no registry at {repo.registry_file} (run: {cli_name} init)")
        return checks
    add(OK, "repository", repo.repo_root)

    if not os.path.isdir(repo.scripts_root):
        add(FAIL, "scripts dir", f"missing: {repo.scripts_root}")
    legacy_registry = os.path.join(repo.repo_root, runtime.LEGACY_REGISTRY_BASENAME)
    if os.path.isfile(legacy_registry) and os.path.basename(repo.registry_file) != runtime.LEGACY_REGISTRY_BASENAME:
        add(WARN, "legacy registry", f"{legacy_registry} is ignored; rename it to {runtime.DEFAULT_REGISTRY_BASENAME}")

    entries = registry_entries if registry_entries is not None else runtime.read_registry(repo)
    add(OK, "registered", f"{len(entries)} script(s)")

    exts = {os.path.splitext(entry)[1].lower() for entry in entries}
    if exts & {".sh", ".bash"}:
        from toolrack import shells

        bash = _working_bash(shells.resolve_bash_exe())
        add(OK if bash else FAIL, "bash", bash or "registered .sh/.bash scripts need a working bash")
    if ".sql" in exts:
        psql = shutil.which("psql")
        add(OK if psql else FAIL, "psql", psql or "registered .sql scripts run through psql, which is not on PATH")

    wrapper = shutil.which(cli_name)
    add(OK if wrapper else WARN, "command on PATH", wrapper or f"'{cli_name}' not found on PATH (open a new shell?)")

    ok, diagnostics = check_repository(repo)
    errors = [d for d in diagnostics if d.level == "error"]
    warnings = [d for d in diagnostics if d.level == "warning"]
    if errors:
        add(FAIL, "validate", f"{len(errors)} error(s); run: {cli_name} core validate")
    elif warnings:
        add(WARN, "validate", f"{len(warnings)} warning(s); run: {cli_name} core validate")
    else:
        add(OK, "validate", f"{len(ok)} script(s) valid")
    return checks


def _working_bash(candidate: str) -> str | None:
    import subprocess

    try:
        done = subprocess.run(
            [candidate, "-c", "echo toolrack"], capture_output=True, text=True, timeout=15, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return candidate if done.returncode == 0 and done.stdout.strip() == "toolrack" else None
