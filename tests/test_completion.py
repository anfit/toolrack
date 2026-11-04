"""The completion fast path must agree exactly with Click's own completion."""

from __future__ import annotations

import json
import os
import subprocess
import sys

import click
import pytest
from click.shell_completion import BashComplete, FishComplete, ZshComplete

from toolrack import completion, runtime
from toolrack import cli as cli_module
from toolrack.cli import _discover_cli_entries, _load_aliases, _read_registry, _write_cache

SHELLS = {"bash": BashComplete, "zsh": ZshComplete, "fish": FishComplete}


@pytest.fixture()
def scripts_repo(repo, make_script, make_sidecar, aliases_cfg, monkeypatch):
    """A repo with groups, nested groups, aliases, every argument kind."""
    make_script("jira/update.py")
    make_sidecar(
        "jira/update.py",
        {
            "description": "Update issues in the tracker. Long second sentence goes here.",
            "args": [
                {"name": "issue", "positional": True, "type": "path", "help": "Issue file."},
                {"name": "output", "choices": ["keys", "json", "jsonl"], "help": "Format."},
                {"name": "dry_run", "flag": True, "option": "--dry-run", "help": "No writes."},
                {"name": "verbose", "flag": True, "option": "-v"},
                {"name": "tag", "multiple": True, "help": "Repeatable."},
                {"name": "count", "type": "int"},
            ],
        },
    )
    make_script("jira/show.py")
    make_sidecar(
        "jira/show.py",
        {
            "description": "Show.",
            "args": [{"name": "files", "positional": True, "variadic": True, "type": "path"}],
        },
    )
    make_script("refactor_tools/tests/analyze.py")
    make_sidecar(
        "refactor_tools/tests/analyze.py",
        {"description": "Analyze tests\n\nwith a second paragraph."},
    )
    make_script("top_level.py")
    make_sidecar("top_level.py", {"description": "A top-level command, deliberately long enough."})
    make_script("my_dir/thing.sh")
    make_sidecar("my_dir/thing.sh", {})
    aliases_cfg({"refactor_tools": "refactor"})

    repo["registry"].write_text(
        "\n".join(
            [
                "scripts/jira/update.py",
                "scripts/jira/show.py",
                "scripts/refactor_tools/tests/analyze.py",
                "scripts/top_level.py",
                "scripts/my_dir/thing.sh",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    registry = _read_registry()
    _write_cache(registry, _discover_cli_entries(registry, _load_aliases()))

    monkeypatch.setattr(cli_module, "_DYNAMIC_CLI_SIGNATURE", None)
    cli_module._clear_dynamic_cli_tree()
    for name, value in {
        "TOOLRACK_CLI_NAME": cli_module.CLI_NAME,
        "TOOLRACK_REPO_ROOT": str(repo["root"]),
        "TOOLRACK_SCRIPTS_ROOT": str(repo["scripts"]),
        "TOOLRACK_REGISTRY_FILE": str(repo["registry"]),
        "TOOLRACK_CACHE_FILE": str(repo["cache"]),
        "TOOLRACK_ALIASES_FILE": str(repo["aliases"]),
    }.items():
        monkeypatch.setenv(name, value)
    return repo


def _request(monkeypatch, shell: str, line: str) -> None:
    words = line.split()
    cword = len(words) if line.endswith(" ") else len(words) - 1
    monkeypatch.setenv(cli_module.COMPLETION_VAR, f"{shell}_complete")
    monkeypatch.setenv("COMP_WORDS", line)
    monkeypatch.setenv("COMP_CWORD", str(cword if shell != "fish" else (words[-1] if cword < len(words) else "")))


def _click_answer(shell: str) -> str:
    comp = SHELLS[shell](cli_module.cli, {}, cli_module.CLI_NAME, cli_module.COMPLETION_VAR)
    return comp.complete() + "\n"


# Lines the fast path must hand to Click rather than answer itself.
FALLBACK = [
    "my environments ",  # unknown to the tree
    "my jira nonexistent ",
    "my jira update --help ",
    "my jira update --bogus ",
    "my --help ",
]

HANDLED = [
    "my ",
    "my c",
    "my j",
    "my t",
    "my -",
    "my --v",
    "my r",
    "my refactor ",
    "my refactor t",
    "my refactor tests ",
    "my refactor tests a",
    "my refactor tests analyze ",
    "my refactor tests analyze --",
    "my core ",
    "my core r",
    "my core -",
    "my core install-completion ",
    "my core install-completion z",
    "my core auto-register ",
    "my core auto-register --",
    "my core auto-register --dry-run --",
    "my core register ",
    "my core register x",
    "my jira ",
    "my jira u",
    "my jira update ",
    "my jira update i",
    "my jira update --",
    "my jira update --o",
    "my jira update --output ",
    "my jira update --output j",
    "my jira update --output=j",
    "my jira update --output=",
    "my jira update --output json --",
    "my jira update --dry-run --d",
    "my jira update --dry-run --",
    "my jira update --tag a --t",
    "my jira update --tag a --tag b --",
    "my jira update X --",
    "my jira update X Y ",
    "my jira update -v -",
    "my jira update -",
    "my jira update --count ",
    "my jira update --count 3 --",
    "my jira update -- ",
    "my jira update -- --",
    "my jira show ",
    "my jira show a b ",
    "my jira show a b --",
    "my top-level ",
    "my top-level --",
    "my my-dir ",
    "my my-dir t",
]


@pytest.mark.parametrize("shell", sorted(SHELLS))
@pytest.mark.parametrize("line", HANDLED)
def test_fast_path_matches_click(scripts_repo, monkeypatch, capsys, shell, line):
    _request(monkeypatch, shell, line)

    assert completion.try_fast_completion() is True
    fast = capsys.readouterr().out

    assert fast == _click_answer(shell)


@pytest.mark.parametrize("line", FALLBACK)
def test_unmodelled_requests_defer_to_click(scripts_repo, monkeypatch, capsys, line):
    _request(monkeypatch, "bash", line)

    assert completion.try_fast_completion() is False
    assert capsys.readouterr().out == ""


def test_ignores_non_completion_modes(scripts_repo, monkeypatch):
    monkeypatch.setenv(cli_module.COMPLETION_VAR, "bash_source")

    assert completion.try_fast_completion() is False


def test_falls_back_when_cache_is_stale(scripts_repo, monkeypatch):
    _request(monkeypatch, "bash", "my jira ")
    assert completion.try_fast_completion() is True

    with open(scripts_repo["scripts"] / "jira" / "update.yml", "a", encoding="utf-8") as handle:
        handle.write("\n")

    assert completion.try_fast_completion() is False


def test_falls_back_when_cache_is_missing(scripts_repo, monkeypatch):
    scripts_repo["cache"].unlink()
    _request(monkeypatch, "bash", "my jira ")

    assert completion.try_fast_completion() is False


def test_falls_back_when_aliases_change(scripts_repo, monkeypatch, aliases_cfg):
    _request(monkeypatch, "bash", "my ")
    assert completion.try_fast_completion() is True

    aliases_cfg({"refactor_tools": "refactoring"})

    assert completion.try_fast_completion() is False


def test_core_table_matches_click_core_group():
    core = cli_module.core
    assert set(completion.CORE_COMMANDS) == set(core.commands)
    for name, command in core.commands.items():
        spec = completion.CORE_COMMANDS[name]
        assert spec["description"] == command.help
        declared = {a["name"]: a for a in spec.get("args", [])}
        assert set(declared) == {p.name for p in command.params}
        for param in command.params:
            arg = declared[param.name]
            if isinstance(param, click.Argument):
                assert arg.get("positional")
            else:
                assert arg.get("flag") == param.is_flag
                assert arg.get("option") == param.opts[0]
                assert arg.get("help") == param.help
            if isinstance(param.type, click.Choice):
                assert arg["choices"] == list(param.type.choices)
    assert completion.CORE_GROUP_HELP == core.help


@pytest.mark.parametrize(
    "text",
    [
        "",
        "Short.",
        "Print a greeting.",
        "A somewhat longer description without a period at the end of it",
        "First sentence ends here. Second sentence follows afterwards.",
        "Exactly forty-five characters long, no stop xx",
        "Para one\nwraps lines.\n\nPara two.",
        "Averyveryveryveryveryveryveryveryverylongsingleword that keeps going",
    ],
)
def test_short_help_matches_click(text):
    assert completion._short_help(text) == click.utils.make_default_short_help(text, 45)


def test_runtime_matches_cli_globals(monkeypatch, tmp_path):
    for name in (
        "TOOLRACK_REGISTRY_BASENAME",
        "TOOLRACK_REGISTRY_FILE",
        "TOOLRACK_SCRIPTS_ROOT",
        "TOOLRACK_CACHE_FILE",
        "TOOLRACK_ALIASES_FILE",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("TOOLRACK_REPO_ROOT", str(tmp_path))
    repo = runtime.resolve_repository()

    assert repo.registry_file == os.path.join(str(tmp_path), ".toolrack")
    assert repo.cache_file == repo.registry_file + ".cache.json"
    assert repo.scripts_root == os.path.join(str(tmp_path), "scripts")


def test_python_module_completion_does_not_import_click(scripts_repo):
    env = os.environ.copy()
    env.update(
        {
            "TOOLRACK_CLI_NAME": "toolrack",
            "TOOLRACK_REPO_ROOT": str(scripts_repo["root"]),
            "TOOLRACK_SCRIPTS_ROOT": str(scripts_repo["scripts"]),
            "TOOLRACK_REGISTRY_FILE": str(scripts_repo["registry"]),
            "TOOLRACK_CACHE_FILE": str(scripts_repo["cache"]),
            "TOOLRACK_ALIASES_FILE": str(scripts_repo["aliases"]),
            "_TOOLRACK_COMPLETE": "bash_complete",
            "COMP_WORDS": "toolrack jira update --output j",
            "COMP_CWORD": "4",
        }
    )
    program = (
        "import json, runpy, sys\n"
        "runpy.run_module('toolrack', run_name='__main__')\n"
        "print(json.dumps(sorted(m for m in sys.modules if m.split('.')[0] in {'click', 'yaml'})))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", program], env=env, capture_output=True, text=True, check=True
    )

    lines = result.stdout.splitlines()
    assert lines[0] == "plain,json"
    assert lines[1] == "plain,jsonl"
    assert json.loads(lines[-1]) == []
