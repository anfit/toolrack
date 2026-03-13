"""Shared pytest fixtures for toolrack tests."""

import os
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


@pytest.fixture()
def repo(tmp_path, monkeypatch):
    """Create an isolated fake scripts repo rooted at tmp_path."""
    import toolrack.cli as cli

    scripts = tmp_path / "scripts"
    scripts.mkdir()
    registry = tmp_path / ".toolrack"

    monkeypatch.setattr(cli, "REPO_ROOT", str(tmp_path))
    monkeypatch.setattr(cli, "SCRIPTS_ROOT", str(scripts))
    monkeypatch.setattr(cli, "REGISTRY_FILE", str(registry))

    return {"root": tmp_path, "scripts": scripts, "registry": registry}


@pytest.fixture()
def make_script(repo):
    """Factory for minimal runnable stubs inside repo['scripts']."""

    def _make(rel_path: str, content: str = "") -> str:
        path = repo["scripts"] / rel_path.lstrip("/")
        path.parent.mkdir(parents=True, exist_ok=True)
        if not content:
            ext = os.path.splitext(rel_path)[1].lower().lstrip(".")
            content = {
                "py": "#!/usr/bin/env python3\nprint('ok')\n",
                "sh": "#!/usr/bin/env bash\necho ok\n",
                "bash": "#!/usr/bin/env bash\necho ok\n",
                "sql": "SELECT 1;\n",
            }.get(ext, "")
        path.write_text(content, encoding="utf-8")
        return str(path)

    return _make


@pytest.fixture()
def make_sidecar(repo):
    """Factory for writing a .yml sidecar next to a script."""
    import yaml

    def _make(rel_script: str, data: dict) -> str:
        path = (repo["scripts"] / rel_script.lstrip("/")).with_suffix(".yml")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            yaml.dump(data, default_flow_style=False, allow_unicode=True),
            encoding="utf-8",
        )
        return str(path)

    return _make


@pytest.fixture()
def aliases_cfg(repo):
    """Factory for writing scripts/aliases.cfg with a [groups] section."""

    def _make(mapping: dict) -> str:
        lines = ["[groups]\n"] + [f"{key} = {value}\n" for key, value in mapping.items()]
        path = repo["scripts"] / "aliases.cfg"
        path.write_text("".join(lines), encoding="utf-8")
        return str(path)

    return _make


@pytest.fixture()
def runner():
    from click.testing import CliRunner

    return CliRunner()
