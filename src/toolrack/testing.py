"""Helpers for testing a toolrack scripts repository.

These run the real CLI in a subprocess (``python -m toolrack``) with the
``TOOLRACK_*`` environment pointing at a repository, so a test exercises exactly
what a user would run, including sidecar parsing, argument handling and the
script itself. Nothing is monkeypatched.

    repo = ToolrackRepo.create(tmp_path)
    repo.add_script("greet/hello.py", "print('hi')", {"description": "Say hi."})
    assert repo.run("greet", "hello").stdout.strip() == "hi"
    repo.validate()          # raises AssertionError listing every problem

The pytest fixtures in :mod:`toolrack.pytest_plugin` wrap these.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple


class RunResult(NamedTuple):
    args: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    def check(self) -> "RunResult":
        """Return self, or raise AssertionError with full output on failure."""
        if not self.ok:
            raise AssertionError(
                f"toolrack {' '.join(self.args)} exited {self.returncode}\n"
                f"--- stdout ---\n{self.stdout}\n--- stderr ---\n{self.stderr}"
            )
        return self


_DEFAULT_CONTENT = {
    ".py": "print('ok')\n",
    ".sh": "#!/usr/bin/env bash\necho ok\n",
    ".bash": "#!/usr/bin/env bash\necho ok\n",
    ".sql": "SELECT 1;\n",
}


def bash_available() -> bool:
    """True when a working ``bash`` can run scripts.

    Checks by running it: on Windows ``bash`` on PATH is often the WSL stub,
    which exists but fails when no distribution is installed.
    """
    from toolrack import cli

    try:
        done = subprocess.run(
            [cli._resolve_bash_exe(), "-c", "echo toolrack"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return done.returncode == 0 and done.stdout.strip() == "toolrack"


class StubCommand:
    """A fake executable on the test PATH that records how it was called."""

    def __init__(self, path: Path, log: Path) -> None:
        self.path = path
        self._log = log

    def calls(self) -> list[dict]:
        """Every invocation so far: ``{"argv": [...], "env": {...}}``."""
        import json

        if not self._log.is_file():
            return []
        return [json.loads(line) for line in self._log.read_text(encoding="utf-8").splitlines() if line]


class ToolrackRepo:
    """A scripts repository (real or temporary) driven through the real CLI.

    Supports every script kind toolrack runs: Python (``.py``), Bash
    (``.sh``/``.bash``) and SQL (``.sql``, which toolrack runs as
    ``psql -f <file>``; fake ``psql`` with :meth:`stub_executable`).
    """

    def __init__(self, root: str | os.PathLike[str], cli_name: str = "toolrack") -> None:
        self.root = Path(root).resolve()
        self.cli_name = cli_name

    # -- construction -----------------------------------------------------

    @classmethod
    def create(cls, root: str | os.PathLike[str]) -> "ToolrackRepo":
        """Make an empty repository (``scripts/`` plus an empty registry)."""
        repo = cls(root)
        repo.scripts.mkdir(parents=True, exist_ok=True)
        repo.registry.touch()
        return repo

    @classmethod
    def discover(cls, start: str | os.PathLike[str] | None = None) -> "ToolrackRepo":
        """Find the repository containing ``start`` (default: cwd), like the CLI does."""
        from toolrack import runtime

        cwd = os.getcwd()
        try:
            if start is not None:
                os.chdir(start)
            return cls(runtime.resolve_repository().repo_root)
        finally:
            os.chdir(cwd)

    # -- layout -----------------------------------------------------------

    @property
    def scripts(self) -> Path:
        return self.root / "scripts"

    @property
    def registry(self) -> Path:
        return self.root / ".toolrack"

    @property
    def aliases(self) -> Path:
        return self.root / "aliases.cfg"

    def registered(self) -> list[str]:
        if not self.registry.is_file():
            return []
        lines = (line.strip() for line in self.registry.read_text(encoding="utf-8").splitlines())
        return [line for line in lines if line and not line.startswith("#")]

    # -- building a repo --------------------------------------------------

    def add_script(
        self,
        rel_path: str,
        content: str | None = None,
        sidecar: dict | None = None,
        register: bool = True,
    ) -> Path:
        """Write a script (and optional YAML sidecar) under ``scripts/``.

        ``sidecar`` is dumped as the adjacent ``.yml``. With ``register=True``
        the repo-relative path is appended to the registry. Writing files
        directly keeps this independent of the commands under test.
        """
        import yaml

        path = self.scripts / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        if content is None:
            content = _DEFAULT_CONTENT.get(path.suffix.lower(), "")
        # newline="\n": Bash must never see CRLF, whatever the host OS.
        path.write_text(content, encoding="utf-8", newline="\n")
        if sidecar is not None:
            path.with_suffix(".yml").write_text(
                yaml.safe_dump(sidecar, sort_keys=False, allow_unicode=True), encoding="utf-8"
            )
        if register:
            entry = path.relative_to(self.root).as_posix()
            if entry not in self.registered():
                with open(self.registry, "a", encoding="utf-8") as handle:
                    handle.write(entry + "\n")
        return path

    def write_aliases(self, groups: dict[str, str]) -> Path:
        lines = ["[groups]"] + [f"{name} = {alias}" for name, alias in groups.items()]
        self.aliases.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return self.aliases

    def stub_executable(
        self, name: str, stdout: str = "", exit_code: int = 0, record_env: tuple[str, ...] = ()
    ) -> StubCommand:
        """Put a fake ``name`` first on PATH for :meth:`run`, recording each call.

        Use it for tools your scripts or toolrack shell out to, most notably
        ``psql`` for ``.sql`` scripts, so tests need no real database.
        ``record_env`` lists environment variables to capture per call (for
        example ``("PGDATABASE",)``). POSIX only.
        """
        if os.name == "nt":
            raise NotImplementedError(
                "stub_executable needs a POSIX host: Windows cannot launch a script shim "
                "by bare name without a compiled .exe"
            )
        bin_dir = self.root / ".toolrack-test-bin"
        bin_dir.mkdir(exist_ok=True)
        log = bin_dir / f"{name}.calls.jsonl"
        shim = bin_dir / name
        shim.write_text(
            f"#!{sys.executable}\n"
            "import json, os, sys\n"
            f"record = {{'argv': sys.argv[1:], 'env': {{k: os.environ.get(k) for k in {list(record_env)!r}}}}}\n"
            f"open({str(log)!r}, 'a', encoding='utf-8').write(json.dumps(record) + '\\n')\n"
            f"sys.stdout.write({stdout!r})\n"
            f"sys.exit({exit_code})\n",
            encoding="utf-8",
            newline="\n",
        )
        shim.chmod(0o755)
        return StubCommand(shim, log)

    # -- running ----------------------------------------------------------

    def env(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        env = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith("TOOLRACK_") and not key.endswith("_COMPLETE")
        }
        env.update(
            TOOLRACK_CLI_NAME=self.cli_name,
            TOOLRACK_REPO_ROOT=str(self.root),
            TOOLRACK_SCRIPTS_ROOT=str(self.scripts),
            TOOLRACK_REGISTRY_FILE=str(self.registry),
            PYTHONIOENCODING="utf-8",
        )
        stubs = self.root / ".toolrack-test-bin"
        if stubs.is_dir():
            env["PATH"] = str(stubs) + os.pathsep + env.get("PATH", "")
        env.update(extra or {})
        return env

    def run(
        self,
        *args: str,
        input: str | None = None,
        env: dict[str, str] | None = None,
        timeout: float = 60,
    ) -> RunResult:
        """Run ``toolrack <args>`` against this repository."""
        completed = subprocess.run(
            [sys.executable, "-m", "toolrack", *args],
            input=input,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=self.env(env),
            cwd=self.root,
            timeout=timeout,
            check=False,
        )
        return RunResult(args, completed.returncode, completed.stdout, completed.stderr)

    def validate(self, strict: bool = False) -> RunResult:
        """Run ``core validate``; raise AssertionError listing problems if it fails."""
        args = ["core", "validate", *(["--strict"] if strict else [])]
        return self.run(*args).check()
