"""
Tests for toolrack/cli.py

Covers:
  - Registry read / write / path resolution
  - Sidecar load / generate / validation rules
  - Alias loading and collision detection
  - CLI commands: register, unregister, list, reregister
  - Command-tree building: groups, aliases, --help content, env section, choices
"""
import os
import pytest

import toolrack.cli as cli
from toolrack.cli import (
    _read_registry,
    _write_registry,
    _resolve_script_path,
    _abs_script_path,
    _normalize_env_path,
    _load_sidecar,
    _generate_sidecar,
    _validate_sidecar,
    _load_aliases,
    _build_cli_tree,
)


def _sample_windows_path(*parts: str) -> str:
    return os.path.normpath(os.path.join("C:\\", *parts))


# ===========================================================================
# Registry
# ===========================================================================

class TestRegistry:

    def test_read_returns_empty_list_when_file_absent(self, repo):
        assert _read_registry() == []

    def test_write_then_read_roundtrip(self, repo):
        _write_registry(["scripts/a.py", "scripts/b.sh"])
        assert _read_registry() == ["scripts/a.py", "scripts/b.sh"]

    def test_read_skips_blank_lines(self, repo):
        repo["registry"].write_text("\nscripts/a.py\n\n", encoding="utf-8")
        assert _read_registry() == ["scripts/a.py"]

    def test_read_skips_comment_lines(self, repo):
        repo["registry"].write_text("# comment\nscripts/a.py\n", encoding="utf-8")
        assert _read_registry() == ["scripts/a.py"]

    def test_write_overwrites_existing(self, repo):
        _write_registry(["scripts/a.py"])
        _write_registry(["scripts/b.py"])
        assert _read_registry() == ["scripts/b.py"]


# ===========================================================================
# Path resolution
# ===========================================================================

class TestResolvePath:

    def test_relative_path_within_repo(self, repo, monkeypatch):
        monkeypatch.chdir(repo["root"])
        assert _resolve_script_path("scripts/github/check_review.py") == \
               "scripts/github/check_review.py"

    def test_strips_leading_dot_slash(self, repo, monkeypatch):
        monkeypatch.chdir(repo["root"])
        assert _resolve_script_path("./scripts/github/check_review.py") == \
               "scripts/github/check_review.py"

    def test_result_uses_forward_slashes(self, repo, monkeypatch):
        monkeypatch.chdir(repo["root"])
        result = _resolve_script_path("scripts/a/b.py")
        assert "\\" not in result

    def test_abs_script_path_resolves_to_repo_root(self, repo):
        result = _abs_script_path("scripts/github/check_review.py")
        assert result == os.path.normpath(
            os.path.join(str(repo["root"]), "scripts/github/check_review.py")
        )


class TestEnvPathNormalization:

    def test_returns_input_unchanged_off_windows(self, monkeypatch):
        monkeypatch.setattr(cli.os, "name", "posix")
        sample = "/cygdrive/c/example/tools/.toolrack"
        assert _normalize_env_path(sample) == sample

    def test_converts_cygwin_drive_path_on_windows(self, monkeypatch):
        monkeypatch.setattr(cli.os, "name", "nt")
        result = _normalize_env_path("/cygdrive/c/example/tools/.toolrack")
        assert result == _sample_windows_path("example", "tools", ".toolrack")

    def test_converts_git_bash_drive_path_on_windows(self, monkeypatch):
        monkeypatch.setattr(cli.os, "name", "nt")
        result = _normalize_env_path("/c/example/tools/scripts")
        assert result == _sample_windows_path("example", "tools", "scripts")


# ===========================================================================
# Sidecar: load / generate
# ===========================================================================

class TestSidecarLoad:

    def test_returns_none_when_no_yml(self, make_script):
        path = make_script("group/tool.py")
        assert _load_sidecar(path) is None

    def test_returns_dict_when_yml_present(self, repo, make_script, make_sidecar):
        make_script("group/tool.py")
        make_sidecar("group/tool.py", {"description": "A tool."})
        abs_path = str(repo["scripts"] / "group" / "tool.py")
        result = _load_sidecar(abs_path)
        assert result == {"description": "A tool."}

    def test_raises_on_non_mapping_yaml(self, repo, make_script):
        make_script("group/tool.py")
        yml = repo["scripts"] / "group" / "tool.yml"
        yml.write_text("- just\n- a\n- list\n", encoding="utf-8")
        with pytest.raises(RuntimeError, match="mapping"):
            _load_sidecar(str(repo["scripts"] / "group" / "tool.py"))

    def test_raises_on_malformed_yaml(self, repo, make_script):
        make_script("group/tool.py")
        yml = repo["scripts"] / "group" / "tool.yml"
        yml.write_text("{bad yaml: [unclosed\n", encoding="utf-8")
        with pytest.raises(Exception):
            _load_sidecar(str(repo["scripts"] / "group" / "tool.py"))


class TestSidecarGenerate:

    def test_creates_yml_file_on_disk(self, make_script):
        path = make_script("group/tool.py")
        _generate_sidecar(path)
        yml = os.path.splitext(path)[0] + ".yml"
        assert os.path.isfile(yml)

    def test_returns_dict_with_description(self, make_script):
        path = make_script("group/tool.py")
        data = _generate_sidecar(path)
        assert "description" in data

    def test_generated_sidecar_has_variadic_passthrough_arg(self, make_script):
        path = make_script("group/tool.sh")
        data = _generate_sidecar(path)
        args = data.get("args", [])
        assert len(args) == 1
        assert args[0].get("variadic") is True
        assert args[0].get("positional") is True

    def test_generated_file_is_loadable(self, make_script):
        path = make_script("group/tool.py")
        _generate_sidecar(path)
        result = _load_sidecar(path)
        assert isinstance(result, dict)

    def test_generated_sidecar_mentions_current_cli_name(self, make_script, monkeypatch):
        path = make_script("group/tool.py")
        monkeypatch.setattr(cli, "CLI_NAME", "my-tools")
        data = _generate_sidecar(path)
        assert "my-tools core register" in data["# autogenerated"]


# ===========================================================================
# Sidecar: validation rules
# ===========================================================================

class TestValidateSidecar:

    def test_valid_empty_sidecar_passes(self, make_script):
        path = make_script("g/tool.py")
        _validate_sidecar({}, path)  # no exception

    def test_valid_full_sidecar_passes(self, make_script):
        path = make_script("g/tool.py")
        sidecar = {
            "description": "ok",
            "args": [
                {"name": "user", "required": True},
                {"name": "pr",   "type": "int", "required": True},
            ],
        }
        _validate_sidecar(sidecar, path)  # no exception

    def test_sql_with_args_raises(self, make_script):
        path = make_script("g/q.sql")
        with pytest.raises(RuntimeError, match="SQL"):
            _validate_sidecar({"args": [{"name": "x"}]}, path)

    def test_sql_with_empty_args_passes(self, make_script):
        path = make_script("g/q.sql")
        _validate_sidecar({"description": "ok", "args": []}, path)

    def test_duplicate_arg_name_raises(self, make_script):
        path = make_script("g/tool.py")
        with pytest.raises(RuntimeError, match="duplicate"):
            _validate_sidecar(
                {"args": [{"name": "a"}, {"name": "a"}]}, path
            )

    def test_variadic_not_positional_raises(self, make_script):
        path = make_script("g/tool.py")
        with pytest.raises(RuntimeError, match="positional"):
            _validate_sidecar({"args": [{"name": "a", "variadic": True}]}, path)

    def test_arg_after_variadic_raises(self, make_script):
        path = make_script("g/tool.py")
        with pytest.raises(RuntimeError, match="variadic must be last"):
            _validate_sidecar({"args": [
                {"name": "a", "variadic": True, "positional": True},
                {"name": "b"},
            ]}, path)

    def test_multiple_and_positional_raises(self, make_script):
        path = make_script("g/tool.py")
        with pytest.raises(RuntimeError, match="multiple"):
            _validate_sidecar(
                {"args": [{"name": "a", "multiple": True, "positional": True}]}, path
            )

    def test_flag_with_non_bool_type_raises(self, make_script):
        path = make_script("g/tool.py")
        with pytest.raises(RuntimeError, match="flag"):
            _validate_sidecar(
                {"args": [{"name": "a", "flag": True, "type": "int"}]}, path
            )

    def test_flag_with_bool_type_passes(self, make_script):
        path = make_script("g/tool.py")
        _validate_sidecar({"args": [{"name": "a", "flag": True, "type": "bool"}]}, path)

    def test_flag_without_type_passes(self, make_script):
        path = make_script("g/tool.py")
        _validate_sidecar({"args": [{"name": "a", "flag": True}]}, path)


# ===========================================================================
# Aliases
# ===========================================================================

class TestAliases:

    def test_no_cfg_returns_empty(self, repo):
        assert _load_aliases() == {}

    def test_reads_groups_section(self, aliases_cfg):
        aliases_cfg({"environments": "env", "refactoring": "refactor"})
        a = _load_aliases()
        assert a["environments"] == "env"
        assert a["refactoring"] == "refactor"

    def test_missing_groups_section_returns_empty(self, repo):
        (repo["scripts"] / "aliases.cfg").write_text("[other]\nfoo = bar\n")
        assert _load_aliases() == {}

    def test_collision_raises(self, aliases_cfg):
        aliases_cfg({"environments": "env", "extras": "env"})
        with pytest.raises(RuntimeError, match="collision"):
            _load_aliases()


# ===========================================================================
# register command
# ===========================================================================

class TestRegister:

    def test_register_with_existing_sidecar(self, repo, runner, make_script, make_sidecar, monkeypatch):
        monkeypatch.chdir(repo["root"])
        make_script("github/check_review.py")
        make_sidecar("github/check_review.py", {"description": "Check a review."})
        result = runner.invoke(cli.cli, ["core", "register", "scripts/github/check_review.py"])
        assert result.exit_code == 0, result.output
        assert "Registered" in result.output
        assert "scripts/github/check_review.py" in _read_registry()

    def test_register_without_sidecar_autogenerates(self, repo, runner, make_script, monkeypatch):
        monkeypatch.chdir(repo["root"])
        make_script("github/list_prs.py")
        result = runner.invoke(cli.cli, ["core", "register", "scripts/github/list_prs.py"])
        assert result.exit_code == 0, result.output
        assert "generated" in result.output.lower()
        assert (repo["scripts"] / "github" / "list_prs.yml").exists()

    def test_register_autogenerated_sidecar_is_loadable(self, repo, runner, make_script, monkeypatch):
        monkeypatch.chdir(repo["root"])
        make_script("github/list_prs.py")
        runner.invoke(cli.cli, ["core", "register", "scripts/github/list_prs.py"])
        path = str(repo["scripts"] / "github" / "list_prs.py")
        data = _load_sidecar(path)
        assert isinstance(data, dict)

    def test_register_missing_script_fails(self, repo, runner, monkeypatch):
        monkeypatch.chdir(repo["root"])
        result = runner.invoke(cli.cli, ["core", "register", "scripts/nonexistent.py"])
        assert result.exit_code != 0
        assert "not found" in result.output.lower()

    def test_register_unsupported_extension_fails(self, repo, runner, make_script, monkeypatch):
        monkeypatch.chdir(repo["root"])
        make_script("group/tool.rb", content="puts 'hi'")
        result = runner.invoke(cli.cli, ["core", "register", "scripts/group/tool.rb"])
        assert result.exit_code != 0

    def test_register_already_registered_is_idempotent(self, repo, runner, make_script, make_sidecar, monkeypatch):
        monkeypatch.chdir(repo["root"])
        make_script("github/check_review.py")
        make_sidecar("github/check_review.py", {"description": "ok"})
        runner.invoke(cli.cli, ["core", "register", "scripts/github/check_review.py"])
        result = runner.invoke(cli.cli, ["core", "register", "scripts/github/check_review.py"])
        assert result.exit_code == 0
        assert "Already" in result.output
        assert _read_registry().count("scripts/github/check_review.py") == 1

    def test_register_invalid_sidecar_fails(self, repo, runner, make_script, monkeypatch):
        monkeypatch.chdir(repo["root"])
        make_script("github/tool.py")
        yml = repo["scripts"] / "github" / "tool.yml"
        yml.parent.mkdir(parents=True, exist_ok=True)
        yml.write_text(
            "description: ok\nargs:\n  - name: a\n    flag: true\n    type: int\n",
            encoding="utf-8",
        )
        result = runner.invoke(cli.cli, ["core", "register", "scripts/github/tool.py"])
        assert result.exit_code != 0
        assert "flag" in result.output.lower()

    def test_register_adds_leading_dotslash_path(self, repo, runner, make_script, make_sidecar, monkeypatch):
        monkeypatch.chdir(repo["root"])
        make_script("github/check_review.py")
        make_sidecar("github/check_review.py", {"description": "ok"})
        runner.invoke(cli.cli, ["core", "register", "./scripts/github/check_review.py"])
        registry = _read_registry()
        assert len(registry) == 1
        assert "\\" not in registry[0]


# ===========================================================================
# auto-register command
# ===========================================================================

class TestAutoRegister:

    def test_registers_unregistered_scripts(self, repo, runner, make_script, make_sidecar):
        make_script("github/check_review.py")
        make_sidecar("github/check_review.py", {"description": "Check a review."})
        make_script("jira/fetch_issue.py")
        make_sidecar("jira/fetch_issue.py", {"description": "Fetch an issue."})
        result = runner.invoke(cli.cli, ["core", "auto-register"])
        assert result.exit_code == 0, result.output
        assert "ADD" in result.output
        registry = _read_registry()
        assert "scripts/github/check_review.py" in registry
        assert "scripts/jira/fetch_issue.py" in registry

    def test_skips_already_registered(self, repo, runner, make_script, make_sidecar, monkeypatch):
        monkeypatch.chdir(repo["root"])
        make_script("github/check_review.py")
        make_sidecar("github/check_review.py", {"description": "ok"})
        runner.invoke(cli.cli, ["core", "register", "scripts/github/check_review.py"])
        result = runner.invoke(cli.cli, ["core", "auto-register"])
        assert result.exit_code == 0, result.output
        assert "SKIP" in result.output
        assert _read_registry().count("scripts/github/check_review.py") == 1

    def test_dry_run_does_not_write(self, repo, runner, make_script, make_sidecar):
        make_script("github/check_review.py")
        make_sidecar("github/check_review.py", {"description": "ok"})
        result = runner.invoke(cli.cli, ["core", "auto-register", "--dry-run"])
        assert result.exit_code == 0, result.output
        assert "[dry-run]" in result.output
        assert _read_registry() == []

    def test_dry_run_reports_would_add(self, repo, runner, make_script, make_sidecar):
        make_script("github/check_review.py")
        make_sidecar("github/check_review.py", {"description": "ok"})
        result = runner.invoke(cli.cli, ["core", "auto-register", "--dry-run"])
        assert "Would add 1" in result.output

    def test_skips_yml_without_matching_script(self, repo, runner):
        yml = repo["scripts"] / "orphan.yml"
        yml.write_text("description: orphan\n", encoding="utf-8")
        result = runner.invoke(cli.cli, ["core", "auto-register"])
        assert result.exit_code == 0
        assert _read_registry() == []

    def test_reports_invalid_sidecar_as_error(self, repo, runner, make_script):
        make_script("github/bad.py")
        yml = repo["scripts"] / "github" / "bad.yml"
        yml.parent.mkdir(parents=True, exist_ok=True)
        yml.write_text(
            "description: ok\nargs:\n  - name: x\n    variadic: true\n",
            encoding="utf-8",
        )
        result = runner.invoke(cli.cli, ["core", "auto-register"])
        assert result.exit_code == 0
        assert "ERROR" in result.output
        assert _read_registry() == []

    def test_summary_line_present(self, repo, runner, make_script, make_sidecar):
        make_script("github/check_review.py")
        make_sidecar("github/check_review.py", {"description": "ok"})
        result = runner.invoke(cli.cli, ["core", "auto-register"])
        assert "Added 1" in result.output


# ===========================================================================
# unregister command
# ===========================================================================

class TestUnregister:

    def test_unregister_removes_entry(self, repo, runner, make_script, make_sidecar):
        make_script("github/check_review.py")
        make_sidecar("github/check_review.py", {"description": "ok"})
        runner.invoke(cli.cli, ["core", "register", "scripts/github/check_review.py"])
        result = runner.invoke(cli.cli, ["core", "unregister", "scripts/github/check_review.py"])
        assert result.exit_code == 0
        assert _read_registry() == []

    def test_unregister_leaves_sidecar_on_disk(self, repo, runner, make_script, make_sidecar):
        make_script("github/check_review.py")
        make_sidecar("github/check_review.py", {"description": "ok"})
        runner.invoke(cli.cli, ["core", "register", "scripts/github/check_review.py"])
        runner.invoke(cli.cli, ["core", "unregister", "scripts/github/check_review.py"])
        assert (repo["scripts"] / "github" / "check_review.yml").exists()

    def test_unregister_not_registered_fails(self, repo, runner):
        result = runner.invoke(cli.cli, ["core", "unregister", "scripts/nonexistent.py"])
        assert result.exit_code != 0
        assert "not registered" in result.output.lower()


# ===========================================================================
# list command
# ===========================================================================

class TestList:

    def test_list_empty_registry(self, repo, runner):
        result = runner.invoke(cli.cli, ["core", "list"])
        assert result.exit_code == 0
        assert "No scripts" in result.output

    def test_list_shows_registered_script(self, repo, runner, make_script, make_sidecar):
        make_script("github/check_review.py")
        make_sidecar("github/check_review.py", {"description": "ok"})
        runner.invoke(cli.cli, ["core", "register", "scripts/github/check_review.py"])
        result = runner.invoke(cli.cli, ["core", "list"])
        assert "check_review.py" in result.output
        assert "[ok]" in result.output

    def test_list_flags_missing_script(self, repo, runner):
        repo["registry"].write_text("scripts/github/gone.py\n", encoding="utf-8")
        result = runner.invoke(cli.cli, ["core", "list"])
        assert "MISSING" in result.output

    def test_list_flags_missing_sidecar(self, repo, runner, make_script):
        make_script("github/no_sidecar.py")
        repo["registry"].write_text("scripts/github/no_sidecar.py\n", encoding="utf-8")
        result = runner.invoke(cli.cli, ["core", "list"])
        assert "no sidecar" in result.output.lower()


# ===========================================================================
# reregister command
# ===========================================================================

class TestReregister:

    def test_reregister_empty_registry(self, repo, runner):
        result = runner.invoke(cli.cli, ["core", "reregister"])
        assert result.exit_code == 0
        assert "Nothing" in result.output

    def test_reregister_reports_ok(self, repo, runner, make_script, make_sidecar):
        make_script("github/check_review.py")
        make_sidecar("github/check_review.py", {"description": "ok"})
        runner.invoke(cli.cli, ["core", "register", "scripts/github/check_review.py"])
        result = runner.invoke(cli.cli, ["core", "reregister"])
        assert "OK" in result.output
        assert "1 ok" in result.output

    def test_reregister_reports_missing(self, repo, runner):
        repo["registry"].write_text("scripts/gone.py\n", encoding="utf-8")
        result = runner.invoke(cli.cli, ["core", "reregister"])
        assert "MISSING" in result.output

    def test_reregister_reports_invalid_sidecar(self, repo, runner, make_script):
        make_script("github/bad.py")
        yml = repo["scripts"] / "github" / "bad.yml"
        yml.parent.mkdir(parents=True, exist_ok=True)
        yml.write_text("description: ok\nargs:\n  - name: x\n    variadic: true\n",
                       encoding="utf-8")
        repo["registry"].write_text("scripts/github/bad.py\n", encoding="utf-8")
        result = runner.invoke(cli.cli, ["core", "reregister"])
        assert "ERROR" in result.output or "error" in result.output.lower()


# ===========================================================================
# Command tree building
# ===========================================================================

def _register_in_tmp(rel_script_path: str):
    canonical = "scripts/" + rel_script_path.lstrip("/")
    registry = _read_registry()
    if canonical not in registry:
        registry.append(canonical)
        _write_registry(registry)


def _reload_tree():
    builtins = {"core"}
    for name in list(cli.cli.commands.keys()):
        if name not in builtins:
            cli.cli.commands.pop(name)
    _build_cli_tree(_read_registry(), _load_aliases())


class TestCommandTree:

    def test_registered_command_appears_under_group(
            self, repo, runner, make_script, make_sidecar):
        make_script("github/check_review.py")
        make_sidecar("github/check_review.py", {
            "description": "Check a review.",
            "args": [{"name": "pr", "required": True, "type": "int"}],
        })
        _register_in_tmp("github/check_review.py")
        _reload_tree()
        result = runner.invoke(cli.cli, ["github", "--help"])
        assert "check-review" in result.output

    def test_command_help_shows_sidecar_description(
            self, repo, runner, make_script, make_sidecar):
        make_script("github/check_review.py")
        make_sidecar("github/check_review.py", {
            "description": "The unique description text for testing.",
            "args": [{"name": "pr", "required": True, "type": "int"}],
        })
        _register_in_tmp("github/check_review.py")
        _reload_tree()
        result = runner.invoke(cli.cli, ["github", "check-review", "--help"])
        assert "unique description text" in result.output

    def test_underscore_script_name_becomes_hyphenated_command(
            self, repo, runner, make_script, make_sidecar):
        make_script("jira/fetch_issues_by_filter.py")
        make_sidecar("jira/fetch_issues_by_filter.py", {"description": "Fetch issues."})
        _register_in_tmp("jira/fetch_issues_by_filter.py")
        _reload_tree()
        result = runner.invoke(cli.cli, ["jira", "--help"])
        assert "fetch-issues-by-filter" in result.output

    def test_aliased_folder_appears_as_short_name(
            self, repo, runner, make_script, make_sidecar, aliases_cfg):
        aliases_cfg({"environments": "env"})
        make_script("environments/get_status.sh")
        make_sidecar("environments/get_status.sh", {"description": "Status."})
        _register_in_tmp("environments/get_status.sh")
        _reload_tree()
        result = runner.invoke(cli.cli, ["--help"])
        assert "env" in result.output
        result2 = runner.invoke(cli.cli, ["env", "get-status", "--help"])
        assert result2.exit_code == 0
        assert "Status" in result2.output

    def test_env_vars_shown_in_help_epilog(
            self, repo, runner, make_script, make_sidecar):
        make_script("db/query.sh")
        make_sidecar("db/query.sh", {
            "description": "Run a query.",
            "env": [{"name": "PGHOST", "help": "Database host."}],
        })
        _register_in_tmp("db/query.sh")
        _reload_tree()
        result = runner.invoke(cli.cli, ["db", "query", "--help"])
        assert "PGHOST" in result.output

    def test_choices_shown_in_option_help(
            self, repo, runner, make_script, make_sidecar):
        make_script("jira/update.py")
        make_sidecar("jira/update.py", {
            "description": "Update issues.",
            "args": [{"name": "output", "choices": ["keys", "json"], "default": "keys"}],
        })
        _register_in_tmp("jira/update.py")
        _reload_tree()
        result = runner.invoke(cli.cli, ["jira", "update", "--help"])
        assert "keys" in result.output
        assert "json" in result.output

    def test_sql_command_has_help(self, repo, runner, make_script, make_sidecar):
        make_script("db/size.sql")
        make_sidecar("db/size.sql", {
            "description": "Show DB size.",
            "env": [{"name": "PGHOST"}],
        })
        _register_in_tmp("db/size.sql")
        _reload_tree()
        result = runner.invoke(cli.cli, ["db", "size", "--help"])
        assert result.exit_code == 0
        assert "Show DB size" in result.output

    def test_nested_subgroup(self, repo, runner, make_script, make_sidecar):
        make_script("environments/processings/get_status.sh")
        make_sidecar("environments/processings/get_status.sh", {"description": "Status."})
        _register_in_tmp("environments/processings/get_status.sh")
        _reload_tree()
        result = runner.invoke(cli.cli, ["environments", "processings", "--help"])
        assert result.exit_code == 0
        assert "get-status" in result.output

    def test_multiple_option_accepted(self, repo, runner, make_script, make_sidecar):
        make_script("github/check_status.py")
        make_sidecar("github/check_status.py", {
            "description": "Check status.",
            "args": [{"name": "reviewers", "multiple": True, "help": "Reviewer names."}],
        })
        _register_in_tmp("github/check_status.py")
        _reload_tree()
        result = runner.invoke(cli.cli, ["github", "check-status", "--help"])
        assert "reviewers" in result.output.lower()

    def test_warning_on_missing_registered_script(
            self, repo, runner, make_script, make_sidecar):
        make_script("github/check_review.py")
        make_sidecar("github/check_review.py", {"description": "ok"})
        _register_in_tmp("github/check_review.py")
        (repo["scripts"] / "github" / "check_review.py").unlink()
        _reload_tree()  # no exception


class TestDeploymentConfig:

    def test_list_message_uses_configured_cli_name(self, repo, runner, monkeypatch):
        monkeypatch.setattr(cli, "CLI_NAME", "my-tools")
        result = runner.invoke(cli.cli, ["core", "list"])
        assert "my-tools core register" in result.output

    def test_install_completion_uses_configured_completion_var(self, repo, runner, monkeypatch):
        monkeypatch.setattr(cli, "CLI_NAME", "my-tools")
        monkeypatch.setattr(cli, "COMPLETION_VAR", "_MY_TOOLS_COMPLETE")
        result = runner.invoke(cli.cli, ["core", "install-completion", "bash"])
        assert result.exit_code == 0
        assert "_MY_TOOLS_COMPLETE" in result.output

    def test_install_completion_does_not_probe_bash_version(self, repo, runner, monkeypatch):
        monkeypatch.setattr(cli, "CLI_NAME", "my-tools")
        monkeypatch.setattr(cli, "COMPLETION_VAR", "_MY_TOOLS_COMPLETE")

        def fail_check_version(*_args, **_kwargs) -> None:
            raise OSError(22, "requested operation is not supported")

        monkeypatch.setattr("click.shell_completion.BashComplete._check_version", fail_check_version)
        result = runner.invoke(cli.cli, ["core", "install-completion", "bash"])
        assert result.exit_code == 0
        assert "_MY_TOOLS_COMPLETE" in result.output
