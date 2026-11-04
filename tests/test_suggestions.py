"""'Did you mean ...?' for mistyped command names, at every level of the tree."""

import pytest

from toolrack import cli


@pytest.fixture()
def tree(repo, make_script, make_sidecar):
    for rel in ("example/hello.py", "example/goodbye.py", "deploy/release.py"):
        make_script(rel)
        make_sidecar(rel, {"description": rel})
    repo["registry"].write_text(
        "".join(f"scripts/{rel}\n" for rel in ("example/hello.py", "example/goodbye.py", "deploy/release.py")),
        encoding="utf-8",
    )
    cli._clear_dynamic_cli_tree()
    cli._DYNAMIC_CLI_SIGNATURE = None
    yield
    cli._clear_dynamic_cli_tree()
    cli._DYNAMIC_CLI_SIGNATURE = None


def test_top_level_typo_suggests_group(tree, runner):
    result = runner.invoke(cli.cli, ["exmaple"])
    assert result.exit_code == 2
    assert "No such command 'exmaple'. Did you mean 'example'?" in result.output


def test_nested_typo_suggests_command(tree, runner):
    result = runner.invoke(cli.cli, ["example", "helo"])
    assert result.exit_code == 2
    assert "Did you mean 'hello'?" in result.output


def test_core_typo_suggests_core_command(tree, runner):
    result = runner.invoke(cli.cli, ["core", "regster"])
    assert result.exit_code == 2
    # Best match first; unregister/reregister are also close enough to list.
    assert "Did you mean one of: 'register'," in result.output


def test_no_suggestion_when_nothing_is_close(tree, runner):
    result = runner.invoke(cli.cli, ["zzzzzz"])
    assert result.exit_code == 2
    assert "No such command 'zzzzzz'." in result.output
    assert "Did you mean" not in result.output


def test_multiple_close_matches_are_listed(tree, runner):
    result = runner.invoke(cli.cli, ["core", "register-"])
    assert "Did you mean one of: 'register'," in result.output


def test_hidden_alias_groups_are_not_suggested(tree, runner, aliases_cfg):
    aliases_cfg({"deploy": "dep"})
    cli._DYNAMIC_CLI_SIGNATURE = None
    result = runner.invoke(cli.cli, ["deploi"])
    assert "Did you mean 'dep'?" in result.output  # the visible alias, not the hidden original


def test_valid_commands_are_unaffected(tree, runner):
    assert runner.invoke(cli.cli, ["core", "list"]).exit_code == 0
