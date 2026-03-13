# AGENTS.md — toolrack

Generic CLI dispatcher engine. Installed into a scripts repo; provides the `attic` command (or any name configured in `[project.scripts]`).

---

## Layout

```
src/toolrack/
  cli.py        ← entire engine
  __init__.py
  __main__.py   ← python -m toolrack entry point
tests/
  conftest.py         ← repo fixture (monkeypatches REPO_ROOT etc.)
  test_cli.py         ← unit tests for the engine
  test_cross_shell.py ← @pytest.mark.integration — skipped by default
SIDECAR_SPEC.md       ← canonical sidecar format spec
pyproject.toml
```

---

## Key module-level globals (monkeypatched in tests)

| Name | Default value |
|---|---|
| `REPO_ROOT` | `TOOLRACK_REPO_ROOT` env var, or walk-up from cwd to find `.attic`/`.git` |
| `SCRIPTS_ROOT` | `TOOLRACK_SCRIPTS_ROOT` env var, or `REPO_ROOT/scripts` |
| `REGISTRY_FILE` | `REPO_ROOT/.attic` |

---

## Key internal functions

| Function | Returns | Notes |
|---|---|---|
| `_find_repo_root()` | `str` | Walk-up from cwd; fallback to cwd at filesystem root |
| `_read_registry()` | `list[str]` | Skips blanks and `#` comments |
| `_write_registry(paths)` | — | Overwrites `.attic` |
| `_resolve_script_path(raw)` | `str` | Any input → repo-relative, forward slashes |
| `_abs_script_path(entry)` | `str` | Registry entry → absolute path |
| `_load_sidecar(abs_path)` | `dict\|None` | `None` if absent; raises on malformed YAML |
| `_generate_sidecar(abs_path)` | `dict` | Writes passthrough stub, returns it |
| `_validate_sidecar(sidecar, abs_path)` | — | Raises `RuntimeError` on violations |
| `_load_aliases()` | `dict` | Reads repo-root `aliases.cfg [groups]`; raises on collision |
| `_make_command(abs_path, cmd_name, sidecar)` | `click.Command` | Builds typed Click command |
| `_build_cli_tree(registry, aliases)` | — | Attaches all commands to `cli` |

---

## Sidecar validation rules

1. SQL scripts must not declare `args`.
2. Arg `name` values must be unique within a sidecar.
3. `variadic: true` requires `positional: true`.
4. A variadic arg must be the last entry in `args`.
5. An arg cannot be both `multiple: true` and `positional: true`.
6. `flag: true` must not declare `type` (or declare `type: bool` only).

Full spec: [SIDECAR_SPEC.md](SIDECAR_SPEC.md)

---

## Tests

```bash
pytest                              # unit tests (integration skipped)
pytest tests/test_cli.py            # engine unit tests only
pytest -m integration -s            # cross-shell integration tests
```

### Test isolation

`conftest.py` provides a `repo` fixture that monkeypatches `toolrack.cli.REPO_ROOT`, `SCRIPTS_ROOT`, and `REGISTRY_FILE` to `tmp_path`. Tests never touch the real `.attic`.
