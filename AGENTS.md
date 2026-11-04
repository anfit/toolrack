# AGENTS.md — toolrack

Generic CLI dispatcher engine. Installed into a scripts repo; provides the `toolrack` command (or any name set through the wrapper's `TOOLRACK_CLI_NAME`).

---

## Layout

```
src/toolrack/
  entrypoint.py ← process entry point: completion fast path, else the Click CLI
  completion.py ← click-free shell completion answered from the metadata cache
  sidecar.py    ← sidecar load/generate/validate (cli.py keeps aliases for compatibility)
  shells.py     ← Windows/Git Bash/Cygwin path and bash resolution
  scaffold.py   ← `core init` repository scaffolding
  doctor.py     ← `core doctor` checks (pure functions, injected inputs)
  testing.py    ← ToolrackRepo: public helpers that drive the real CLI in a subprocess
  pytest_plugin.py ← pytest11 fixtures for users' tests (toolrack_repo, toolrack_project, ...)
  runtime.py    ← click-free repo discovery, registry reading, cache validation
  cli.py        ← the Click engine (commands, sidecars, dynamic tree)
  __main__.py   ← python -m toolrack entry point
tests/
  conftest.py         ← repo fixture (monkeypatches REPO_ROOT etc.)
  test_cli.py         ← unit tests for the engine
  test_completion.py  ← fast-path vs Click parity tests
  test_cross_shell.py ← @pytest.mark.integration — skipped by default
SIDECAR_SPEC.md       ← canonical sidecar format spec
pyproject.toml
```

---

## Key module-level globals (monkeypatched in tests)

| Name | Default value |
|---|---|
| `REPO_ROOT` | `TOOLRACK_REPO_ROOT` env var, or walk-up from cwd to find `.toolrack` (legacy `.attic`) or `.git` |
| `SCRIPTS_ROOT` | `TOOLRACK_SCRIPTS_ROOT` env var, or `REPO_ROOT/scripts` |
| `REGISTRY_FILE` | `TOOLRACK_REGISTRY_FILE` env var, or `REPO_ROOT/.toolrack` |

All of these are resolved by `runtime.resolve_repository()`; `cli.py` only snapshots the result.

---

## Shell completion fast path

Every <TAB> press runs `python -m toolrack`. `entrypoint.main()` first calls `completion.try_fast_completion()`, which answers from `.toolrack.cache.json` using only the standard library (no Click, no PyYAML). Rules when touching it:

- Keep `runtime.py` and `completion.py` free of `click`/`yaml` imports (a test enforces it) and avoid heavy stdlib imports (`dataclasses`, `inspect`) — each costs milliseconds per keypress.
- The fast path must never guess. If it does not model a request exactly (unknown token, `--help`, a stale/missing cache, hidden alias groups), it returns `None` and Click answers.
- `tests/test_completion.py` compares the fast path with Click byte for byte for bash, zsh and fish. A new sidecar feature or core command needs a case there, and `completion.CORE_COMMANDS` must mirror the real `core` group (a test checks it).
- `testing.py` / `pytest_plugin.py` are public API for people testing their own scripts: keep them stable, subprocess-based (no monkeypatching engine globals), and covering `.py`, `.sh`/`.bash` and `.sql`. `tests/test_testing.py` is their contract; `core validate` (`_check_repository`) is tested in `tests/test_validate.py`.
- Cache validation is shared (`runtime.read_cache`); never re-implement it.
- `core install-completion bash` emits a helper that runs `python -m toolrack` directly instead of the wrapper, with a fallback to the wrapper if the baked interpreter path no longer exists.

---

## Key internal functions

| Function | Returns | Notes |
|---|---|---|
| `_find_repo_root()` | `str` | Walk-up from cwd; fallback to cwd at filesystem root |
| `_read_registry()` | `list[str]` | Skips blanks and `#` comments |
| `_write_registry(paths)` | — | Overwrites `.toolrack` |
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

`conftest.py` provides a `repo` fixture that monkeypatches `toolrack.cli.REPO_ROOT`, `SCRIPTS_ROOT`, and `REGISTRY_FILE` to `tmp_path`. Tests never touch the real `.toolrack`.

---

## Issue resolution procedure

When working an issue from GitHub, use this sequence unless the issue says otherwise:

1. Read the issue directly in GitHub and restate the expected behavior before editing code.
2. Inspect `src/toolrack/cli.py` and the most relevant tests in `tests/test_cli.py` or `tests/test_cross_shell.py`.
3. Prefer a narrow change first, but keep the design direction requested by the issue in mind.
4. Add or update tests for the behavior being changed before closing out the work.
5. Run `pytest` locally. If only part of the suite is relevant during iteration, finish with the default `pytest` command before opening a PR.
6. Commit the implementation on an issue branch, push it, and open a PR that links the issue and summarizes testing.
7. If the work changes team process or repo conventions, follow up with a separate documentation change on `main` so the implementation PR stays focused.
