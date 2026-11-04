"""`core validate`: CI-oriented, read-only repository check."""

from toolrack import cli


def _validate(runner, *extra):
    return runner.invoke(cli.cli, ["core", "validate", *extra])


def _register(repo, *entries):
    repo["registry"].write_text("".join(f"{e}\n" for e in entries), encoding="utf-8")


def test_validate_passes_on_clean_repo(repo, runner, make_script, make_sidecar):
    make_script("tools/hello.py")
    make_sidecar("tools/hello.py", {"description": "hi"})
    _register(repo, "scripts/tools/hello.py")
    cli._refresh_cache()

    result = _validate(runner, "--strict")

    assert result.exit_code == 0, result.output
    assert "1 ok, 0 errors, 0 warnings" in result.output


def test_validate_empty_repo_is_ok(repo, runner):
    assert _validate(runner).exit_code == 0


def test_validate_fails_on_missing_script(repo, runner):
    _register(repo, "scripts/gone.py")
    result = _validate(runner)
    assert result.exit_code == 1
    assert "registered script not found" in result.output


def test_validate_fails_on_missing_sidecar(repo, runner, make_script):
    make_script("tools/bare.py")
    _register(repo, "scripts/tools/bare.py")
    result = _validate(runner)
    assert result.exit_code == 1
    assert "sidecar missing" in result.output


def test_validate_fails_on_invalid_sidecar(repo, runner, make_script, make_sidecar):
    make_script("tools/bad.py")
    make_sidecar("tools/bad.py", {"args": [{"name": "x", "variadic": True}]})
    _register(repo, "scripts/tools/bad.py")
    result = _validate(runner)
    assert result.exit_code == 1
    assert "variadic" in result.output


def test_validate_fails_on_malformed_yaml(repo, runner, make_script):
    make_script("tools/broken.py")
    (repo["scripts"] / "tools" / "broken.yml").write_text("description: [unclosed\n", encoding="utf-8")
    _register(repo, "scripts/tools/broken.py")
    result = _validate(runner)
    assert result.exit_code == 1
    assert "scripts/tools/broken.py" in result.output


def test_validate_reports_every_problem_not_just_the_first(repo, runner):
    _register(repo, "scripts/a.py", "scripts/b.py")
    result = _validate(runner)
    assert "scripts/a.py" in result.output and "scripts/b.py" in result.output
    assert "0 ok, 2 errors" in result.output


def test_validate_fails_on_alias_collision(repo, runner, aliases_cfg):
    aliases_cfg({"one": "x", "two": "x"})
    result = _validate(runner)
    assert result.exit_code == 1
    assert "collision" in result.output


def test_validate_fails_on_duplicate_command_names(repo, runner, make_script, make_sidecar):
    for name in ("hello_world.py", "hello-world.py"):
        make_script(f"tools/{name}")
        make_sidecar(f"tools/{name}", {"description": "x"})
    _register(repo, "scripts/tools/hello_world.py", "scripts/tools/hello-world.py")
    result = _validate(runner)
    assert result.exit_code == 1
    assert "already provided by" in result.output


def test_validate_warns_about_unregistered_pair(repo, runner, make_script, make_sidecar):
    make_script("tools/loose.py")
    make_sidecar("tools/loose.py", {"description": "x"})
    result = _validate(runner)
    assert result.exit_code == 0
    assert "not registered" in result.output
    assert _validate(runner, "--strict").exit_code == 1


def test_validate_warns_on_stale_cache_but_does_not_write(repo, runner, make_script, make_sidecar):
    make_script("tools/hello.py")
    make_sidecar("tools/hello.py", {"description": "hi"})
    _register(repo, "scripts/tools/hello.py")

    result = _validate(runner)

    assert result.exit_code == 0
    assert "cache is missing or stale" in result.output
    assert not repo["cache"].exists()
    assert _validate(runner, "--strict").exit_code == 1
