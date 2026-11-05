"""The --help fast path must agree exactly with Click, or defer to it."""

from __future__ import annotations

import os
import subprocess
import sys

import pytest
from click.testing import CliRunner

from toolrack import cli as cli_module
from toolrack import helpfast, runtime
from toolrack.cli import _discover_cli_entries, _load_aliases, _read_registry, _write_cache

# name -> (sidecar, expected to be served by the fast path)
COMMANDS = {
    "plain": ({"description": "Print a greeting."}, True),
    "empty": ({}, True),
    "opts": (
        {
            "description": "Do things.",
            "args": [
                {"name": "name", "required": True, "help": "Name to greet."},
                {"name": "count", "type": "int", "default": 3, "help": "How many."},
                {"name": "ratio", "type": "float", "help": "Ratio."},
                {"name": "enabled", "type": "bool"},
                {"name": "verbose", "flag": True, "help": "Be loud."},
                {"name": "dry_run", "flag": True, "option": "--dry-run"},
                {"name": "mode", "choices": ["a", "b"], "default": "a"},
                {"name": "out", "type": "path"},
                {"name": "tag", "multiple": True, "help": "Tags."},
                {"name": "nohelp"},
                {"name": "limit", "required": True, "type": "int", "help": "R."},
                {"name": "label", "default": "x", "help": "Label."},
            ],
        },
        True,
    ),
    "pos": (
        {
            "description": "Positional.",
            "args": [
                {"name": "src", "positional": True},
                {"name": "dst", "positional": True, "required": False},
                {"name": "rest", "positional": True, "variadic": True, "required": False},
            ],
        },
        True,
    ),
    "posreq_var": (
        {"description": "x", "args": [{"name": "files", "positional": True, "variadic": True}]},
        True,
    ),
    "mixed": (
        {
            "description": "Mixed.",
            "args": [
                {"name": "target", "positional": True, "help": "ignored for arguments"},
                {"name": "force", "flag": True, "help": "Overwrite."},
            ],
        },
        True,
    ),
    "poschoice": (
        {"description": "x", "args": [{"name": "kind", "positional": True, "choices": ["a", "b"]}]},
        False,
    ),
    "env": ({"description": "Env.", "env": [{"name": "FOO", "help": "A foo."}], "epilog": "Bye."}, False),
    "longdesc": ({"description": "word " * 30}, False),
    "twoparas": ({"description": "One.\n\nTwo."}, False),
    "unicode": ({"description": "Zażółć gęślą jaźń"}, False),
    "longopt": (
        {"description": "x", "args": [{"name": "a_very_long_option_name_indeed_yes", "help": "H."}]},
        False,
    ),
    "wraphelp": ({"description": "x", "args": [{"name": "n", "help": "long " * 30}]}, False),
    "boolflagdefault": ({"description": "x", "args": [{"name": "b", "type": "bool", "default": True}]}, False),
}

PATHS = [
    [],
    ["core"],
    ["demo"],
    ["demo", "nested"],
    ["simple"],
    ["simple", "a"],
    ["simple", "b"],
    ["top-level"],
    *([["demo", name.replace("_", "-")] for name in COMMANDS]),
]


@pytest.fixture()
def tree(repo, make_script, make_sidecar, monkeypatch):
    entries = []
    for name, (sidecar, _fast) in COMMANDS.items():
        make_script(f"demo/{name}.py")
        make_sidecar(f"demo/{name}.py", sidecar)
        entries.append(f"scripts/demo/{name}.py")
    for name, description in (("a", "First."), ("b", "")):
        make_script(f"simple/{name}.py")
        make_sidecar(f"simple/{name}.py", {"description": description})
        entries.append(f"scripts/simple/{name}.py")
    make_script("demo/nested/inner.py")
    make_sidecar("demo/nested/inner.py", {"description": "Inner command."})
    entries.append("scripts/demo/nested/inner.py")
    make_script("top_level.py")
    make_sidecar("top_level.py", {"description": "A top-level command, deliberately long enough to be cut short."})
    entries.append("scripts/top_level.py")
    repo["registry"].write_text("\n".join(entries) + "\n", encoding="utf-8")
    registry = _read_registry()
    _write_cache(registry, _discover_cli_entries(registry, _load_aliases()))
    # try_fast_help locates the repository from the environment, like a real run.
    monkeypatch.setenv("TOOLRACK_REPO_ROOT", str(repo["root"]))
    monkeypatch.setenv("TOOLRACK_SCRIPTS_ROOT", str(repo["scripts"]))
    monkeypatch.setenv("TOOLRACK_REGISTRY_FILE", str(repo["registry"]))
    monkeypatch.setattr(cli_module, "_DYNAMIC_CLI_SIGNATURE", None)
    cli_module._clear_dynamic_cli_tree()
    yield runtime.read_cache(registry, cli_module._current_repo())
    cli_module._clear_dynamic_cli_tree()
    cli_module._DYNAMIC_CLI_SIGNATURE = None


def _click_help(path: list[str], monkeypatch=None, columns: int | None = None) -> str:
    if columns is not None:
        # CliRunner forces a width of 80, so give Click the width its own formula
        # yields for this terminal size: max(min(columns, 80) - 2, 50).
        monkeypatch.setitem(
            cli_module.cli.context_settings, "terminal_width", max(min(columns, 80) - 2, 50)
        )
    result = CliRunner().invoke(cli_module.cli, [*path, "--help"], prog_name=cli_module.CLI_NAME)
    assert result.exit_code == 0, result.output
    return result.output


@pytest.mark.parametrize("columns", [40, 60, 80, 120])
@pytest.mark.parametrize("path", PATHS, ids=lambda p: "/".join(p) or "<root>")
def test_fast_help_matches_click_or_defers(tree, monkeypatch, path, columns):
    monkeypatch.setenv("COLUMNS", str(columns))

    fast = helpfast.render(tree, cli_module.CLI_NAME, path)

    if fast is not None:
        assert fast == _click_help(path, monkeypatch, columns)


@pytest.mark.parametrize("path", PATHS, ids=lambda p: "/".join(p) or "<root>")
def test_expected_cases_are_served_by_the_fast_path(tree, monkeypatch, path):
    monkeypatch.setenv("COLUMNS", "80")
    if path[:1] == ["demo"] and len(path) == 2 and path[1] in {n.replace("_", "-") for n in COMMANDS}:
        expected_fast = COMMANDS[path[1].replace("-", "_")][1]
    elif path == ["demo"]:
        expected_fast = False  # the group lists a command with non-ASCII help
    elif path[:1] == ["core"] and len(path) > 1:
        expected_fast = False
    else:
        expected_fast = True

    fast = helpfast.render(tree, cli_module.CLI_NAME, path)

    assert (fast is not None) == expected_fast, path


def test_core_subcommand_help_is_left_to_click(tree):
    assert helpfast.render(tree, cli_module.CLI_NAME, ["core", "list"]) is None


def test_unknown_tokens_defer(tree):
    assert helpfast.render(tree, cli_module.CLI_NAME, ["nope"]) is None
    assert helpfast.render(tree, cli_module.CLI_NAME, ["demo", "plain", "extra"]) is None


def test_root_help_text_matches_click_group():
    assert helpfast.ROOT_HELP == cli_module.cli.help


def test_try_fast_help_requires_help_as_last_plain_token(tree, capsys):
    assert helpfast.try_fast_help(["demo", "--help", "plain"]) is False
    assert helpfast.try_fast_help(["--verbose"]) is False
    assert helpfast.try_fast_help(["-x", "--help"]) is False


def test_version_matches_click(capsys):
    result = CliRunner().invoke(cli_module.cli, ["--version"], prog_name=cli_module.CLI_NAME)
    assert helpfast.try_fast_help(["--version"]) is True
    assert capsys.readouterr().out == result.output


def test_try_fast_help_prints_and_respects_opt_out(tree, capsys, monkeypatch):
    monkeypatch.setenv("COLUMNS", "80")
    assert helpfast.try_fast_help(["demo", "plain", "--help"]) is True
    assert capsys.readouterr().out == _click_help(["demo", "plain"])
    assert helpfast.try_fast_help(["demo", "plain", "--help"], {"TOOLRACK_NO_FAST_HELP": "1"}) is False


def test_stale_cache_defers(tree, repo):
    repo["registry"].write_text(repo["registry"].read_text() + "scripts/gone.py\n", encoding="utf-8")
    assert helpfast.try_fast_help(["demo", "plain", "--help"]) is False


def test_process_output_is_identical_with_and_without_fast_path(tree, repo):
    env = {
        **os.environ,
        "TOOLRACK_CLI_NAME": "my",
        "TOOLRACK_REPO_ROOT": str(repo["root"]),
        "TOOLRACK_SCRIPTS_ROOT": str(repo["scripts"]),
        "TOOLRACK_REGISTRY_FILE": str(repo["registry"]),
        "COLUMNS": "80",
        # The tree includes a non-ASCII description, which Click (not the fast path)
        # prints; a legacy console codepage on the CI runner cannot encode it.
        "PYTHONIOENCODING": "utf-8",
    }

    def run(extra, *args):
        done = subprocess.run(
            [sys.executable, "-m", "toolrack", *args],
            env={**env, **extra},
            capture_output=True,
            check=True,
        )
        return done.stdout

    assert run({}, "--version") == run({"TOOLRACK_NO_FAST_HELP": "1"}, "--version")
    assert run({}, "--version").startswith(b"my, version ")

    for columns in ("50", "70", "100"):  # real terminal-width handling, no CliRunner involved
        for args in (["--help"], ["demo", "--help"], ["demo", "opts", "--help"], ["demo", "env", "--help"]):
            extra = {"COLUMNS": columns}
            assert run(extra, *args) == run({**extra, "TOOLRACK_NO_FAST_HELP": "1"}, *args), (columns, args)
