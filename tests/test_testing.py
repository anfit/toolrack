"""The public test helpers: ToolrackRepo (real CLI in a subprocess) and its fixtures."""

import pytest

from toolrack.testing import RunResult, ToolrackRepo


def test_add_script_registers_and_runs(tmp_path):
    repo = ToolrackRepo.create(tmp_path)
    repo.add_script(
        "greet/hello.py",
        "import sys\nprint('hello,', sys.argv[2])\n",
        {"description": "Say hi.", "args": [{"name": "name", "required": True}]},
    )

    assert repo.registered() == ["scripts/greet/hello.py"]
    result = repo.run("greet", "hello", "--name", "Ada").check()
    assert result.stdout.strip() == "hello, Ada"


def test_run_returns_script_exit_code(tmp_path):
    repo = ToolrackRepo.create(tmp_path)
    repo.add_script("t/fail.py", "raise SystemExit(7)\n", {"description": "fails"})
    assert repo.run("t", "fail").returncode == 7


def test_validate_passes_then_fails_with_listing(tmp_path):
    repo = ToolrackRepo.create(tmp_path)
    repo.add_script("t/ok.py", sidecar={"description": "ok"})
    repo.run("core", "refresh-cache").check()
    repo.validate(strict=True)

    repo.add_script("t/nosidecar.py")
    with pytest.raises(AssertionError) as info:
        repo.validate()
    assert "scripts/t/nosidecar.py" in str(info.value)


def test_environment_is_isolated_from_the_callers(tmp_path, monkeypatch):
    other = tmp_path / "other"
    other.mkdir()
    monkeypatch.setenv("TOOLRACK_REPO_ROOT", str(other))
    repo = ToolrackRepo.create(tmp_path / "mine")
    repo.add_script("t/x.py", sidecar={"description": "x"})
    assert "scripts/t/x.py" in repo.run("core", "list").stdout


def test_write_aliases(tmp_path):
    repo = ToolrackRepo.create(tmp_path)
    repo.add_script("long_group_name/x.py", sidecar={"description": "x"})
    repo.write_aliases({"long_group_name": "lg"})
    assert repo.run("lg", "x").ok


def test_discover_finds_enclosing_repo(tmp_path):
    repo = ToolrackRepo.create(tmp_path)
    nested = repo.scripts / "deep"
    nested.mkdir()
    assert ToolrackRepo.discover(nested).root == repo.root


def test_run_result_check_includes_output():
    failed = RunResult(("x",), 2, "out", "err")
    with pytest.raises(AssertionError, match="exited 2"):
        failed.check()


def test_default_content_per_script_kind(tmp_path):
    repo = ToolrackRepo.create(tmp_path)
    assert "print" in repo.add_script("a/p.py").read_text()
    assert "echo" in repo.add_script("a/s.sh").read_text()
    assert "SELECT" in repo.add_script("a/q.sql").read_text()
    assert b"\r" not in repo.add_script("a/s.sh").read_bytes()


def test_bash_script_runs_with_typed_args(toolrack_repo, toolrack_require_bash):
    toolrack_repo.add_script(
        "ops/say.sh",
        'echo "got: $2 $3"\n',
        {
            "description": "Echo args.",
            "args": [
                {"name": "name", "required": True},
                {"name": "loud", "flag": True},
            ],
        },
    )
    out = toolrack_repo.run("ops", "say", "--name", "Ada", "--loud").check().stdout
    assert "got: Ada --loud" in out


def test_bash_exit_code_propagates(toolrack_repo, toolrack_require_bash):
    toolrack_repo.add_script("ops/fail.sh", "exit 3\n", {"description": "fails"})
    assert toolrack_repo.run("ops", "fail").returncode == 3


def test_sql_script_is_run_through_psql(toolrack_repo, toolrack_require_posix):
    psql = toolrack_repo.stub_executable("psql", stdout="1\n", record_env=("PGDATABASE",))
    script = toolrack_repo.add_script(
        "db/ping.sql",
        "SELECT 1;\n",
        {"description": "Ping.", "env": [{"name": "PGDATABASE", "required": True}]},
    )

    result = toolrack_repo.run("db", "ping", env={"PGDATABASE": "analytics"}).check()

    assert result.stdout == "1\n"
    (call,) = psql.calls()
    assert call["argv"] == ["-f", str(script)]
    assert call["env"] == {"PGDATABASE": "analytics"}


def test_sql_sidecar_with_args_fails_validation(toolrack_repo):
    toolrack_repo.add_script(
        "db/bad.sql", sidecar={"description": "x", "args": [{"name": "p", "positional": True}]}
    )
    with pytest.raises(AssertionError, match="SQL scripts must not declare"):
        toolrack_repo.validate()


def test_stub_executable_is_rejected_on_windows(tmp_path, monkeypatch):
    monkeypatch.setattr("toolrack.testing.os.name", "nt")
    with pytest.raises(NotImplementedError):
        ToolrackRepo.create(tmp_path).stub_executable("psql")


def test_pytest_fixtures_are_provided_by_the_plugin(toolrack_repo, toolrack_project):
    toolrack_repo.add_script("t/ok.py", sidecar={"description": "ok"})
    assert toolrack_repo.run("t", "ok").ok
    assert toolrack_project.root.is_dir()
