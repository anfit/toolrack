"""`core init` (scaffolding) and `core doctor` (installation diagnostics)."""

from toolrack import cli, doctor, scaffold
from toolrack.testing import ToolrackRepo


def test_init_creates_a_working_repository(tmp_path):
    root = tmp_path / "fresh"
    repo = ToolrackRepo(root)
    root.mkdir()

    result = repo.run("core", "init", str(root)).check()

    assert (root / "scripts" / "example" / "hello.py").is_file()
    assert (root / "scripts" / "example" / "hello.yml").is_file()
    assert repo.registered() == ["scripts/example/hello.py"]
    assert ".toolrack.cache.json" in (root / ".gitignore").read_text()
    assert "CREATED" in result.stdout
    assert repo.run("example", "hello", "--name", "Ada").check().stdout.strip() == "hello, Ada"
    repo.validate()


def test_init_is_idempotent_and_never_overwrites(tmp_path):
    root = tmp_path / "again"
    root.mkdir()
    repo = ToolrackRepo(root)
    repo.run("core", "init", str(root)).check()
    hello = root / "scripts" / "example" / "hello.py"
    hello.write_text("print('mine')\n", encoding="utf-8")

    second = repo.run("core", "init", str(root)).check()

    assert hello.read_text() == "print('mine')\n"
    assert "CREATED" not in second.stdout
    assert repo.registered() == ["scripts/example/hello.py"]
    assert (root / ".gitignore").read_text().count(".toolrack.cache.json") == 1


def test_init_appends_to_existing_gitignore_and_registry(tmp_path):
    (tmp_path / ".gitignore").write_text("dist/", encoding="utf-8")
    (tmp_path / ".toolrack").write_text("scripts/mine.py\n", encoding="utf-8")

    scaffold.scaffold_repository(str(tmp_path))

    assert (tmp_path / ".gitignore").read_text() == "dist/\n.toolrack.cache.json\n"
    assert (tmp_path / ".toolrack").read_text() == "scripts/mine.py\nscripts/example/hello.py\n"


def test_init_no_example(tmp_path):
    scaffold.scaffold_repository(str(tmp_path), example=False)
    assert (tmp_path / "scripts").is_dir()
    assert (tmp_path / ".toolrack").read_text() == ""
    assert not (tmp_path / "scripts" / "example").exists()


def _checks(repo, monkeypatch, legacy=None):
    monkeypatch.setattr(doctor, "legacy_distribution", lambda: legacy)
    return {c.name: c for c in doctor.run_checks(cli._current_repo(), "toolrack", cli._check_repository)}


def test_doctor_reports_missing_repository(repo, monkeypatch):
    checks = _checks(repo, monkeypatch)
    assert checks["repository"].level == doctor.FAIL
    assert "init" in checks["repository"].detail


def test_doctor_healthy_repository(repo, make_script, make_sidecar, monkeypatch):
    make_script("t/a.py")
    make_sidecar("t/a.py", {"description": "a"})
    repo["registry"].write_text("scripts/t/a.py\n", encoding="utf-8")
    cli._refresh_cache()

    checks = _checks(repo, monkeypatch)

    assert checks["repository"].level == doctor.OK
    assert checks["registered"].detail == "1 script(s)"
    assert checks["validate"].level == doctor.OK
    assert "legacy install" not in checks


def test_doctor_flags_validation_errors(repo, monkeypatch):
    repo["registry"].write_text("scripts/gone.py\n", encoding="utf-8")
    assert _checks(repo, monkeypatch)["validate"].level == doctor.FAIL


def test_doctor_warns_about_legacy_distribution(repo, monkeypatch):
    repo["registry"].write_text("", encoding="utf-8")
    check = _checks(repo, monkeypatch, legacy="0.0.1")["legacy install"]
    assert check.level == doctor.WARN
    assert "pip uninstall toolrack" in check.detail


def test_doctor_requires_psql_for_sql_scripts(repo, make_script, make_sidecar, monkeypatch):
    make_script("db/q.sql")
    make_sidecar("db/q.sql", {"description": "q"})
    repo["registry"].write_text("scripts/db/q.sql\n", encoding="utf-8")
    monkeypatch.setattr(doctor.shutil, "which", lambda name: None)
    assert _checks(repo, monkeypatch)["psql"].level == doctor.FAIL


def test_doctor_command_exit_code(tmp_path):
    root = tmp_path / "r"
    root.mkdir()
    repo = ToolrackRepo(root)
    assert repo.run("core", "doctor").returncode == 1
    repo.run("core", "init", str(root)).check()
    result = repo.run("core", "doctor")
    assert "[ok  ] repository" in result.stdout
    assert "failures" in result.stdout
