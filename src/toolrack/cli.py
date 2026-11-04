#!/usr/bin/env python3
"""toolrack - typed dispatcher for a user-owned scripts repository."""

from __future__ import annotations

import configparser
import json
import os
import subprocess
import sys
from dataclasses import dataclass

import click

from toolrack import __version__, runtime, shells, sidecar as _sidecar
from toolrack.runtime import RepositoryContext

_is_windows = shells.is_windows
CLI_NAME = runtime.cli_name()
COMPLETION_VAR = runtime.completion_var(CLI_NAME)
DEFAULT_REGISTRY_BASENAME = runtime.registry_basename()
# TODO(#12): Move environment-derived settings into an explicit runtime config object.
# Import-time globals make packaging harder, complicate tests, and will get in
# the way once toolrack is used as an installed library as well as a checkout.



def _fix_windows_completion_crlf() -> None:
    if not _is_windows():
        return
    if not os.environ.get(COMPLETION_VAR):
        return
    import io

    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer,
        encoding=sys.stdout.encoding or "utf-8",
        errors="replace",
        newline="",
        line_buffering=False,
    )


_fix_windows_completion_crlf()


_find_repo_root = runtime.find_repo_root
_normalize_env_path = runtime.normalize_env_path
_file_state = runtime.file_state

_REPOSITORY = runtime.resolve_repository()
REPO_ROOT = _REPOSITORY.repo_root
SCRIPTS_ROOT = _REPOSITORY.scripts_root
REGISTRY_FILE = _REPOSITORY.registry_file
CACHE_FILE = _REPOSITORY.cache_file
ALIASES_FILE = _REPOSITORY.aliases_file


@dataclass(frozen=True)
class CommandArgSpec:
    name: str
    positional: bool
    flag: bool
    multiple: bool
    variadic: bool
    required: bool
    default: object
    choices: tuple[str, ...]
    help: str
    option: str | None
    type_name: str


@dataclass(frozen=True)
class CommandEnvSpec:
    name: str
    help: str
    default: object
    required: bool


@dataclass(frozen=True)
class CommandSpec:
    script_path: str
    command_name: str
    interpreter: tuple[str, ...]
    description: str
    args: tuple[CommandArgSpec, ...]
    env: tuple[CommandEnvSpec, ...]
    epilog: str


def _current_repo() -> RepositoryContext:
    return RepositoryContext(
        repo_root=REPO_ROOT,
        scripts_root=SCRIPTS_ROOT,
        registry_file=REGISTRY_FILE,
        cache_file=CACHE_FILE,
        aliases_file=ALIASES_FILE,
    )

_SKIP_DIRS = {"__pycache__", ".git", "node_modules"}
_RUNNABLE_EXTS = {".py", ".sh", ".bash", ".sql"}


def _read_registry(repo: RepositoryContext | None = None) -> list[str]:
    return runtime.read_registry(repo or _current_repo())


def _write_registry(paths: list[str], repo: RepositoryContext | None = None) -> None:
    repo = repo or _current_repo()
    with open(repo.registry_file, "w", encoding="utf-8") as handle:
        for path in paths:
            handle.write(path + "\n")


def _resolve_script_path(raw: str, repo: RepositoryContext | None = None) -> str:
    repo = repo or _current_repo()
    if os.path.isabs(raw):
        p = os.path.abspath(raw)
    else:
        repo_candidate = os.path.join(repo.repo_root, raw)
        p = os.path.abspath(repo_candidate if os.path.exists(repo_candidate) else raw)
    try:
        return os.path.relpath(p, repo.repo_root).replace(os.sep, "/")
    except ValueError:
        return p.replace(os.sep, "/")


def _abs_script_path(registry_entry: str, repo: RepositoryContext | None = None) -> str:
    return runtime.abs_script_path(registry_entry, repo or _current_repo())


# TODO(#3): Promote cached entry payloads to typed models instead of raw dicts.
# The JSON contract (signature + entries) lives in toolrack.runtime so the
# completion fast path validates the cache exactly as the CLI does.
def _cache_signature(registry: list[str], repo: RepositoryContext | None = None) -> str:
    return runtime.cache_signature(registry, repo or _current_repo())


def _read_cache(registry: list[str], repo: RepositoryContext | None = None) -> list[dict] | None:
    return runtime.read_cache(registry, repo or _current_repo())


def _write_cache(
    registry: list[str], entries: list[dict], repo: RepositoryContext | None = None
) -> None:
    repo = repo or _current_repo()
    cache_dir = os.path.dirname(repo.cache_file)
    if cache_dir:
        os.makedirs(cache_dir, exist_ok=True)
    payload = {
        "signature": _cache_signature(registry, repo),
        "entries": entries,
    }
    with open(repo.cache_file, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")


def _load_aliases(repo: RepositoryContext | None = None) -> dict[str, str]:
    repo = repo or _current_repo()
    cfg = configparser.ConfigParser()
    # TODO(#4): Merge registry/cache/aliases metadata into one top-level repo
    # manifest object. These files are now conceptually related, but the code
    # still treats them as separate incidental globals.
    if not os.path.isfile(repo.aliases_file):
        return {}
    cfg.read(repo.aliases_file)
    if not cfg.has_section("groups"):
        return {}

    aliases: dict[str, str] = {}
    seen: dict[str, str] = {}
    for original, alias in cfg.items("groups"):
        if alias in seen:
            raise RuntimeError(
                f"aliases.cfg collision: '{original}' and '{seen[alias]}' both map to '{alias}'"
            )
        aliases[original] = alias
        seen[alias] = original
    return aliases


_sidecar_path = runtime.sidecar_path
_to_bash_path = shells.to_bash_path
_resolve_bash_exe = shells.resolve_bash_exe
_load_sidecar = _sidecar.load_sidecar
_validate_sidecar = _sidecar.validate_sidecar


def _generate_sidecar(script_path: str) -> dict:
    return _sidecar.generate_sidecar(script_path, CLI_NAME)




def _interpreter(script_path: str) -> list[str]:
    ext = os.path.splitext(script_path)[1].lower()
    if ext == ".py":
        return [sys.executable]
    if ext in (".sh", ".bash"):
        return [_resolve_bash_exe()]
    if ext == ".sql":
        return ["psql", "-f"]
    return []


def _click_type(type_str: str):
    return {
        "int": click.INT,
        "float": click.FLOAT,
        "bool": click.BOOL,
        "path": click.Path(),
    }.get(type_str, click.STRING)


def _help_epilog(env_specs: tuple[CommandEnvSpec, ...], epilog_text: str) -> str | None:
    epilog_lines: list[str] = []
    if env_specs:
        epilog_lines.append("Environment variables:")
        for env_spec in env_specs:
            line = f"  {env_spec.name}"
            if env_spec.help:
                line += f"  {env_spec.help}"
            if env_spec.default is not None:
                line += f"  [default: {env_spec.default}]"
            if env_spec.required:
                line += "  [required]"
            epilog_lines.append(line)
    if epilog_text:
        if epilog_lines:
            epilog_lines.append("")
        epilog_lines.append(epilog_text)
    return "\n".join(epilog_lines) or None


def _command_arg_spec(arg: dict) -> CommandArgSpec:
    positional = bool(arg.get("positional"))
    return CommandArgSpec(
        name=arg["name"],
        positional=positional,
        flag=bool(arg.get("flag")),
        multiple=bool(arg.get("multiple")),
        variadic=bool(arg.get("variadic")),
        required=arg.get("required", True if positional else False),
        default=arg.get("default"),
        choices=tuple(str(choice) for choice in (arg.get("choices") or [])),
        help=arg.get("help", ""),
        option=arg.get("option"),
        type_name=arg.get("type", "string"),
    )


def _command_env_spec(env_spec: dict) -> CommandEnvSpec:
    return CommandEnvSpec(
        name=env_spec["name"],
        help=env_spec.get("help", ""),
        default=env_spec.get("default"),
        required=bool(env_spec.get("required")),
    )


def _command_spec(script_path: str, cmd_name: str, sidecar: dict) -> CommandSpec:
    args = tuple(_command_arg_spec(arg) for arg in (sidecar.get("args") or []))
    env = tuple(_command_env_spec(env_spec) for env_spec in (sidecar.get("env") or []))
    return CommandSpec(
        script_path=script_path,
        command_name=cmd_name,
        interpreter=tuple(_interpreter(script_path)),
        description=sidecar.get("description", "").strip(),
        args=args,
        env=env,
        epilog=sidecar.get("epilog", "").strip(),
    )


def _click_param(spec: CommandArgSpec):
    click_type = _click_type(spec.type_name)
    if spec.choices:
        click_type = click.Choice(list(spec.choices))
    if spec.flag:
        click_type = click.BOOL

    if spec.positional:
        return click.Argument(
            [spec.name],
            required=spec.required,
            type=click_type,
            nargs=-1 if spec.variadic else 1,
        )
    flag_name = spec.option or f"--{spec.name.replace('_', '-')}"
    if spec.flag:
        return click.Option([flag_name], is_flag=True, default=False, help=spec.help)
    return click.Option(
        [flag_name],
        required=spec.required,
        default=spec.default,
        type=click_type,
        multiple=spec.multiple,
        help=spec.help,
        show_default=spec.default is not None,
    )


def _run_dynamic_command(spec: CommandSpec, kwargs: dict) -> int:
    """Execute a generated command and return the subprocess exit status."""
    bash_exe = spec.interpreter[0] if spec.interpreter else ""
    exec_path = (
        _to_bash_path(spec.script_path, bash_exe)
        if bash_exe == "bash" or bash_exe.endswith("bash.exe")
        else spec.script_path
    )
    cmd = list(spec.interpreter) + [exec_path]
    for arg_spec in spec.args:
        value = kwargs[arg_spec.name]
        flag_name = arg_spec.option or f"--{arg_spec.name.replace('_', '-')}"

        if value is None or value == () or value is False:
            continue
        if arg_spec.positional:
            if arg_spec.variadic:
                for item in value:
                    cmd.append(str(item))
            else:
                cmd.append(str(value))
        elif arg_spec.flag:
            cmd.append(flag_name)
        elif arg_spec.multiple:
            for item in value:
                cmd.extend([flag_name, str(item)])
        else:
            cmd.extend([flag_name, str(value)])

    result = subprocess.run(cmd, stdin=sys.stdin, stdout=sys.stdout, stderr=sys.stderr)
    return result.returncode


def _command_callback(spec: CommandSpec):
    def callback(**kwargs):
        return _run_dynamic_command(spec, kwargs)

    return callback


def _make_command(script_path: str, cmd_name: str, sidecar: dict) -> click.Command:
    spec = _command_spec(script_path, cmd_name, sidecar)

    return click.Command(
        name=spec.command_name,
        callback=_command_callback(spec),
        params=[_click_param(arg_spec) for arg_spec in spec.args],
        help=spec.description,
        epilog=_help_epilog(spec.env, spec.epilog),
    )


def _to_cli_name(name: str) -> str:
    return name.replace("_", "-")


def _script_cli_name(filename: str) -> str:
    return _to_cli_name(os.path.splitext(filename)[0])


def _group_path_and_cmd(
    registry_entry: str, aliases: dict[str, str], repo: RepositoryContext | None = None
) -> tuple[list[str], str, str]:
    abs_path = _abs_script_path(registry_entry, repo)
    parts = registry_entry.replace("\\", "/").split("/")
    if parts[0] == "scripts":
        parts = parts[1:]
    group_parts = parts[:-1]
    filename = parts[-1]
    cmd_name = _script_cli_name(filename)
    resolved = [_to_cli_name(aliases.get(part, part)) for part in group_parts]
    return resolved, cmd_name, abs_path


def _discover_cli_entries(
    registry: list[str], aliases: dict[str, str], repo: RepositoryContext | None = None
) -> list[dict]:
    repo = repo or _current_repo()
    # TODO(#8): This should become a pure discovery phase that reports structured
    # diagnostics instead of printing via Click while traversing the repo.
    # Mixing discovery, validation, and user I/O makes caching and reuse clumsy.
    discovered: list[dict] = []

    for entry in registry:
        abs_path = _abs_script_path(entry, repo)
        if not os.path.isfile(abs_path):
            click.echo(f"[{CLI_NAME}] warning: registered script not found: {entry}", err=True)
            continue

        try:
            sidecar = _load_sidecar(abs_path)
            if sidecar is None:
                click.echo(
                    f"[{CLI_NAME}] warning: sidecar missing for registered script: {entry}\n"
                    f"  Run `{CLI_NAME} core register {entry}` to regenerate it.",
                    err=True,
                )
                continue
            _validate_sidecar(sidecar, abs_path)
        except RuntimeError as exc:
            click.echo(f"[{CLI_NAME}] skipping {entry}: {exc}", err=True)
            continue

        group_parts, cmd_name, _ = _group_path_and_cmd(entry, aliases, repo)
        discovered.append(
            {
                "entry": entry,
                "abs_path": abs_path,
                "group_parts": group_parts,
                "cmd_name": cmd_name,
                "sidecar": sidecar,
            }
        )

    return discovered


def _load_cli_entries(
    registry: list[str], aliases: dict[str, str], repo: RepositoryContext | None = None
) -> list[dict]:
    repo = repo or _current_repo()
    cached = _read_cache(registry, repo)
    if cached is not None:
        return cached
    discovered = _discover_cli_entries(registry, aliases, repo)
    try:
        _write_cache(registry, discovered, repo)
    except OSError:
        pass
    return discovered


def _refresh_cache(repo: RepositoryContext | None = None) -> None:
    repo = repo or _current_repo()
    try:
        registry = _read_registry(repo)
        aliases = _load_aliases(repo)
        _write_cache(registry, _discover_cli_entries(registry, aliases, repo), repo)
    except (OSError, RuntimeError):
        return


def _build_cli_tree(entries: list[dict], aliases: dict[str, str]) -> None:
    # TODO(#9): Build a declarative command tree first, then render it into Click.
    # Right now the tree is mutated directly on the global Click group, which is
    # serviceable for one process but awkward for caching and introspection.
    groups: dict[tuple[str, ...], click.Group] = {}

    def get_or_create_group(name_parts: list[str]) -> click.Group:
        key = tuple(name_parts)
        if key in groups:
            return groups[key]

        if len(name_parts) == 1:
            name = name_parts[0]
            folder = name
            for original, alias in aliases.items():
                if _to_cli_name(alias) == name:
                    folder = original
                    break
            group = click.Group(name=name, help=f"Commands from scripts/{folder}/")
            cli.add_command(group)

            for original, alias in aliases.items():
                if _to_cli_name(alias) == name and _to_cli_name(original) != name:
                    shadow = click.Group(
                        name=_to_cli_name(original),
                        help=f"Alias; use '{name}' instead.",
                        hidden=True,
                    )
                    cli.add_command(shadow)
                    groups[(_to_cli_name(original),)] = shadow
                    break

            groups[key] = group
            return group

        parent = get_or_create_group(name_parts[:-1])
        name = name_parts[-1]
        if name in parent.commands:
            return parent.commands[name]
        folder = name
        for original, alias in aliases.items():
            if _to_cli_name(alias) == name:
                folder = original
                break
        group = click.Group(name=name, help=f"Commands from scripts/{'/'.join(name_parts)}/")
        parent.add_command(group)
        groups[key] = group
        return group

    for entry in entries:
        group_parts = entry["group_parts"]
        cmd_name = entry["cmd_name"]
        abs_path = entry["abs_path"]
        sidecar = entry["sidecar"]
        try:
            cmd = _make_command(abs_path, cmd_name, sidecar)
        except RuntimeError as exc:
            click.echo(f"[{CLI_NAME}] error building command for {entry['entry']}: {exc}", err=True)
            continue

        if group_parts:
            get_or_create_group(group_parts).add_command(cmd)
        else:
            cli.add_command(cmd)


_STATIC_COMMANDS = {"core"}
_DYNAMIC_CLI_SIGNATURE: str | None = None


def _clear_dynamic_cli_tree() -> None:
    for name in list(cli.commands.keys()):
        if name not in _STATIC_COMMANDS:
            cli.commands.pop(name)


def _ensure_cli_tree() -> None:
    global _DYNAMIC_CLI_SIGNATURE

    repo = _current_repo()
    registry = _read_registry(repo)
    signature = _cache_signature(registry, repo)
    if _DYNAMIC_CLI_SIGNATURE == signature:
        return

    _clear_dynamic_cli_tree()
    aliases = _load_aliases(repo)
    _build_cli_tree(_load_cli_entries(registry, aliases, repo), aliases)
    _DYNAMIC_CLI_SIGNATURE = signature


class RuntimeCLIGroup(click.Group):
    def get_command(self, ctx: click.Context, cmd_name: str) -> click.Command | None:
        if cmd_name not in self.commands:
            _ensure_cli_tree()
        return super().get_command(ctx, cmd_name)

    def list_commands(self, ctx: click.Context) -> list[str]:
        _ensure_cli_tree()
        return super().list_commands(ctx)


@click.group(cls=RuntimeCLIGroup)
@click.version_option(version=__version__, prog_name=CLI_NAME)
def cli():
    """Typed dispatcher for a user-owned scripts repository."""


@cli.result_callback()
def _propagate_command_exit_status(result, **_kwargs):
    if type(result) is int:
        raise click.exceptions.Exit(result)
    return result
# TODO(#10): Avoid import-time CLI tree construction. It speeds up the "single file
# script" model, but it also means import has side effects and front-loads work
# even for commands that only need core maintenance operations.


@cli.group("core")
def core():
    """Registry management and shell-completion commands."""


@core.command("register")
@click.argument("script_path")
def cmd_register(script_path: str):
    """Register a script as a CLI subcommand."""
    repo = _current_repo()
    canonical = _resolve_script_path(script_path, repo)
    abs_path = _abs_script_path(canonical, repo)

    if not os.path.isfile(abs_path):
        raise click.ClickException(f"Script not found: {abs_path}")

    ext = os.path.splitext(abs_path)[1].lower()
    if ext not in _RUNNABLE_EXTS:
        raise click.ClickException(
            f"Unsupported extension '{ext}'. Supported: {', '.join(sorted(_RUNNABLE_EXTS))}"
        )

    try:
        sidecar = _load_sidecar(abs_path)
    except RuntimeError as exc:
        raise click.ClickException(str(exc))

    if sidecar is None:
        sidecar = _generate_sidecar(abs_path)
        click.echo(
            f"No sidecar found; generated: {_sidecar_path(abs_path)}\n"
            "  Arguments are passed through verbatim. Edit the sidecar to add\n"
            "  typed options and proper help."
        )
    else:
        try:
            _validate_sidecar(sidecar, abs_path)
        except RuntimeError as exc:
            raise click.ClickException(str(exc))

    registry = _read_registry(repo)
    if canonical in registry:
        click.echo(f"Already registered: {canonical}")
        return

    registry.append(canonical)
    _write_registry(registry, repo)
    _refresh_cache(repo)
    click.echo(f"Registered: {canonical}")


@core.command("auto-register")
@click.option("--dry-run", is_flag=True, help="Show what would be registered without writing.")
def cmd_auto_register(dry_run: bool):
    """Scan scripts/ for sidecar+script pairs and register unregistered ones."""
    repo = _current_repo()
    registry = _read_registry(repo)
    registry_set = set(registry)
    added: list[str] = []
    skipped: list[str] = []
    failures: list[tuple[str, str]] = []

    for dirpath, dirnames, filenames in os.walk(repo.scripts_root):
        dirnames[:] = sorted(
            dirname for dirname in dirnames if dirname not in _SKIP_DIRS and not dirname.startswith(".")
        )
        for filename in sorted(filenames):
            if not filename.endswith(".yml"):
                continue
            stem = os.path.splitext(filename)[0]
            script_path = None
            for ext in _RUNNABLE_EXTS:
                candidate = os.path.join(dirpath, stem + ext)
                if os.path.isfile(candidate):
                    script_path = candidate
                    break
            if script_path is None:
                continue

            canonical = _resolve_script_path(script_path, repo)
            if canonical in registry_set:
                skipped.append(canonical)
                continue

            try:
                sidecar = _load_sidecar(script_path)
                if sidecar is None:
                    failures.append((canonical, "sidecar could not be loaded"))
                    continue
                _validate_sidecar(sidecar, script_path)
            except RuntimeError as exc:
                failures.append((canonical, str(exc)))
                continue

            added.append(canonical)
            if not dry_run:
                registry.append(canonical)
                registry_set.add(canonical)

    if not dry_run:
        _write_registry(registry, repo)
        _refresh_cache(repo)

    prefix = "[dry-run] " if dry_run else ""
    for canonical in skipped:
        click.echo(f"  SKIP     {canonical}  (already registered)")
    for canonical in added:
        click.echo(f"  {prefix}ADD      {canonical}")
    for canonical, reason in failures:
        click.echo(f"  ERROR    {canonical}: {reason}", err=True)

    action = "Would add" if dry_run else "Added"
    click.echo(f"\n{action} {len(added)}, skipped {len(skipped)}, {len(failures)} errors.")


@core.command("unregister")
@click.argument("script_path")
def cmd_unregister(script_path: str):
    """Remove a script from the registry."""
    repo = _current_repo()
    canonical = _resolve_script_path(script_path, repo)
    registry = _read_registry(repo)

    if canonical not in registry:
        raise click.ClickException(f"Not registered: {canonical}")

    registry.remove(canonical)
    _write_registry(registry, repo)
    _refresh_cache(repo)
    click.echo(f"Unregistered: {canonical}")


@core.command("reregister")
def cmd_reregister():
    """Re-validate all registered commands."""
    repo = _current_repo()
    registry = _read_registry(repo)
    if not registry:
        click.echo("Nothing registered.")
        return

    _load_aliases(repo)
    ok = 0
    errors = 0
    for entry in registry:
        abs_path = _abs_script_path(entry, repo)
        if not os.path.isfile(abs_path):
            click.echo(f"  MISSING  {entry}", err=True)
            errors += 1
            continue
        try:
            sidecar = _load_sidecar(abs_path)
            if sidecar is None:
                click.echo(
                    f"  NO SIDECAR  {entry}  (run: {CLI_NAME} core register {entry})",
                    err=True,
                )
                errors += 1
                continue
            _validate_sidecar(sidecar, abs_path)
            click.echo(f"  OK       {entry}")
            ok += 1
        except RuntimeError as exc:
            click.echo(f"  ERROR    {entry}: {exc}", err=True)
            errors += 1

    _refresh_cache(repo)
    click.echo(f"\n{ok} ok, {errors} errors.")


@dataclass(frozen=True)
class Diagnostic:
    level: str  # "error" or "warning"
    subject: str
    message: str


def _check_repository(repo: RepositoryContext) -> tuple[list[str], list[Diagnostic]]:
    """Inspect the whole repository without writing anything.

    Returns the registry entries that are fully valid and the diagnostics for
    everything else. Unlike discovery at runtime (which skips broken entries
    with a warning), this reports every problem so CI can fail on it.
    """
    import yaml

    diagnostics: list[Diagnostic] = []
    ok: list[str] = []
    registry = _read_registry(repo)

    try:
        aliases = _load_aliases(repo)
    except RuntimeError as exc:
        diagnostics.append(Diagnostic("error", os.path.basename(repo.aliases_file), str(exc)))
        aliases = {}

    claimed: dict[tuple[str, ...], str] = {}
    for entry in registry:
        abs_path = _abs_script_path(entry, repo)
        if not os.path.isfile(abs_path):
            diagnostics.append(Diagnostic("error", entry, "registered script not found"))
            continue
        ext = os.path.splitext(abs_path)[1].lower()
        if ext not in _RUNNABLE_EXTS:
            diagnostics.append(Diagnostic("error", entry, f"unsupported extension '{ext}'"))
            continue
        try:
            sidecar = _load_sidecar(abs_path)
            if sidecar is None:
                diagnostics.append(
                    Diagnostic("error", entry, f"sidecar missing (run: {CLI_NAME} core register {entry})")
                )
                continue
            _validate_sidecar(sidecar, abs_path)
            group_parts, cmd_name, _ = _group_path_and_cmd(entry, aliases, repo)
            _command_spec(abs_path, cmd_name, sidecar)
        except (RuntimeError, ValueError, yaml.YAMLError) as exc:
            diagnostics.append(Diagnostic("error", entry, str(exc)))
            continue

        key = (*group_parts, cmd_name)
        if key in claimed:
            diagnostics.append(
                Diagnostic(
                    "error",
                    entry,
                    f"command '{' '.join(key)}' is already provided by {claimed[key]}",
                )
            )
            continue
        claimed[key] = entry
        ok.append(entry)

    registered = set(registry)
    for dirpath, dirnames, filenames in os.walk(repo.scripts_root):
        dirnames[:] = sorted(
            name for name in dirnames if name not in _SKIP_DIRS and not name.startswith(".")
        )
        for filename in sorted(filenames):
            if not filename.endswith(".yml"):
                continue
            stem = os.path.join(dirpath, os.path.splitext(filename)[0])
            for script_ext in sorted(_RUNNABLE_EXTS):
                if os.path.isfile(stem + script_ext):
                    canonical = _resolve_script_path(stem + script_ext, repo)
                    if canonical not in registered:
                        diagnostics.append(
                            Diagnostic(
                                "warning",
                                canonical,
                                f"has a sidecar but is not registered (run: {CLI_NAME} core auto-register)",
                            )
                        )
                    break

    if registry and _read_cache(registry, repo) is None:
        diagnostics.append(
            Diagnostic(
                "warning",
                os.path.basename(repo.cache_file),
                f"cache is missing or stale (run: {CLI_NAME} core refresh-cache)",
            )
        )
    return ok, diagnostics


@core.command("validate")
@click.option("--strict", is_flag=True, help="Treat warnings as errors.")
def cmd_validate(strict: bool):
    """Check registry, sidecars and aliases without changing anything (for CI)."""
    ok, diagnostics = _check_repository(_current_repo())
    for entry in ok:
        click.echo(f"  OK       {entry}")
    errors = warnings = 0
    for item in diagnostics:
        if item.level == "error":
            errors += 1
            click.echo(f"  ERROR    {item.subject}: {item.message}", err=True)
        else:
            warnings += 1
            click.echo(f"  WARNING  {item.subject}: {item.message}", err=True)
    click.echo(f"\n{len(ok)} ok, {errors} errors, {warnings} warnings.")
    if errors or (strict and warnings):
        raise click.exceptions.Exit(1)


@core.command("init")
@click.argument("path", default=".", required=False)
@click.option("--no-example", is_flag=True, help="Do not create the example script.")
def cmd_init(path: str, no_example: bool):
    """Create a minimal scripts repository (never overwrites existing files)."""
    from toolrack import scaffold

    root = os.path.abspath(path)
    try:
        results = scaffold.scaffold_repository(root, example=not no_example)
    except OSError as exc:
        raise click.ClickException(str(exc))
    for rel, status in results:
        click.echo(f"  {status.upper():<8} {rel}")
    repo = scaffold.repository_context(root)
    _refresh_cache(repo)
    click.echo(
        f"\nRepository ready at {root}\n"
        f"Next: cd there, then run `python -m toolrack --help`"
        + ("" if no_example else " and `python -m toolrack example hello --name you`")
        + ".\nAdd a script plus a .yml sidecar under scripts/, then `core auto-register`."
    )


@core.command("doctor")
def cmd_doctor():
    """Diagnose the toolrack installation and repository setup."""
    from toolrack import doctor

    checks = doctor.run_checks(_current_repo(), CLI_NAME, _check_repository)
    for check in checks:
        tag = {"ok": "ok  ", "warn": "WARN", "fail": "FAIL"}[check.level]
        click.echo(f"  [{tag}] {check.name}: {check.detail}", err=check.level != "ok")
    failed = sum(1 for c in checks if c.level == doctor.FAIL)
    warned = sum(1 for c in checks if c.level == doctor.WARN)
    click.echo(f"\n{len(checks) - failed - warned} ok, {warned} warnings, {failed} failures.")
    if failed:
        raise click.exceptions.Exit(1)


@core.command("refresh-cache")
def cmd_refresh_cache():
    """Rebuild the command metadata cache next to the registry."""
    repo = _current_repo()
    _refresh_cache(repo)
    click.echo(f"Refreshed cache: {repo.cache_file}")


@core.command("list")
def cmd_list():
    """List all registered scripts."""
    repo = _current_repo()
    registry = _read_registry(repo)
    if not registry:
        click.echo(f"No scripts registered. Use: {CLI_NAME} core register <script_path>")
        return
    for entry in registry:
        abs_path = _abs_script_path(entry, repo)
        status = "ok" if os.path.isfile(abs_path) else "MISSING"
        sidecar_exists = os.path.isfile(_sidecar_path(abs_path))
        sidecar_tag = "" if sidecar_exists else "  [no sidecar]"
        click.echo(f"  [{status}]  {entry}{sidecar_tag}")


def _direct_completion_target() -> tuple[str, dict[str, str]]:
    """Interpreter and environment the wrapper would use, for direct completion."""
    repo = _current_repo()
    python = _to_bash_path(sys.executable, _resolve_bash_exe())
    env = {
        "TOOLRACK_CLI_NAME": CLI_NAME,
        "TOOLRACK_REPO_ROOT": repo.repo_root,
        "TOOLRACK_SCRIPTS_ROOT": repo.scripts_root,
        "TOOLRACK_REGISTRY_FILE": repo.registry_file,
        "TOOLRACK_CACHE_FILE": repo.cache_file,
        "TOOLRACK_ALIASES_FILE": repo.aliases_file,
    }
    return python, env


def _direct_completion_helper_name() -> str:
    ident = "".join(ch if ch.isalnum() else "_" for ch in CLI_NAME)
    return f"_{ident}_completion_direct"


def _bypass_wrapper_in_zsh_completion(source: str) -> str:
    """Zsh counterpart of the Bash bypass; same fallback to the wrapper.

    Only rewrites the exact invocation Click emits and otherwise returns the
    script unchanged, so an unexpected Click version degrades to the old path.
    """
    import shlex

    call = (
        f'env COMP_WORDS="${{words[*]}}" COMP_CWORD=$((CURRENT-1)) '
        f"{COMPLETION_VAR}=zsh_complete {CLI_NAME}"
    )
    if call not in source:
        return source

    python, env = _direct_completion_target()
    assignments = " ".join(f"{name}={shlex.quote(value)}" for name, value in env.items())
    helper = _direct_completion_helper_name()
    prefix = f'env COMP_WORDS="$1" COMP_CWORD="$2" {COMPLETION_VAR}=zsh_complete'
    definition = (
        f"{helper}() {{\n"
        f"    if [ -x {shlex.quote(python)} ]; then\n"
        f"        {prefix} {assignments} {shlex.quote(python)} -m toolrack\n"
        f"    else\n"
        f"        {prefix} {shlex.quote(CLI_NAME)}\n"
        f"    fi\n"
        f"}}\n\n"
    )
    source = source.replace(call, f'{helper} "${{words[*]}}" $((CURRENT-1))', 1)
    # `#compdef` must stay the first line of the file.
    if source.startswith("#compdef"):
        first, _, rest = source.partition("\n")
        return f"{first}\n\n{definition}{rest}"
    return definition + source


def _bypass_wrapper_in_fish_completion(source: str) -> str:
    """Fish counterpart of the Bash bypass; same fallback to the wrapper."""
    call = (
        f"env {COMPLETION_VAR}=fish_complete COMP_WORDS=(commandline -cp) "
        f"COMP_CWORD=(commandline -t) {CLI_NAME}"
    )
    if call not in source:
        return source

    python, env = _direct_completion_target()
    # Single-quoted fish strings cannot represent an embedded apostrophe
    # portably; leave the script alone rather than emit a broken one.
    if any("'" in value for value in (python, *env.values())):
        return source
    assignments = " ".join(f"{name}='{value}'" for name, value in env.items())
    helper = _direct_completion_helper_name()
    prefix = f'env COMP_WORDS="$argv[1]" COMP_CWORD="$argv[2]" {COMPLETION_VAR}=fish_complete'
    definition = (
        f"function {helper}\n"
        f"    if test -x '{python}'\n"
        f"        {prefix} {assignments} '{python}' -m toolrack\n"
        f"    else\n"
        f"        {prefix} {CLI_NAME}\n"
        f"    end\n"
        f"end\n\n"
    )
    source = source.replace(call, f"{helper} (commandline -cp) (commandline -t)", 1)
    return definition + source


def _bypass_wrapper_in_bash_completion(source: str) -> str:
    """Make Bash completion call this interpreter directly instead of the wrapper.

    Click's script runs ``$1`` (the wrapper on PATH) for every <TAB>; on Windows
    that starts a whole extra bash process. The generated helper runs
    ``python -m toolrack`` with the same environment the wrapper would export,
    and falls back to ``$1`` if that interpreter no longer exists (venv moved or
    recreated), so a stale script degrades to the old behaviour, not to nothing.
    """
    import shlex

    call = " $1)"
    if call not in source:
        return source

    python, env = _direct_completion_target()
    assignments = " ".join(f"{name}={shlex.quote(value)}" for name, value in env.items())
    helper = _direct_completion_helper_name()
    definition = (
        f"{helper}() {{\n"
        f"    if [ -x {shlex.quote(python)} ]; then\n"
        f"        {assignments} {shlex.quote(python)} -m toolrack\n"
        f"    else\n"
        f'        "$1"\n'
        f"    fi\n"
        f"}}\n\n"
    )
    return definition + source.replace(call, f' {helper} "$1")', 1)


@core.command("install-completion")
@click.argument("shell", type=click.Choice(["bash", "zsh", "fish"]), default="bash")
def install_completion(shell: str):
    """Print the shell completion script to stdout."""
    from click.shell_completion import BashComplete, FishComplete, ShellComplete, ZshComplete

    shell_cls = {"bash": BashComplete, "zsh": ZshComplete, "fish": FishComplete}[shell]
    comp = shell_cls(cli, {}, CLI_NAME, COMPLETION_VAR)
    # Use the base formatter directly so we don't shell out to probe the
    # user's Bash installation when merely printing the completion script.
    source = ShellComplete.source(comp).replace("\r\n", "\n").replace("\r", "\n")
    if shell == "bash":
        # TODO(#11): Stop patching upstream Click output with string replacement.
        # This is pragmatic for now, but a custom completion emitter would be
        # more explicit and less brittle than editing generated shell text.
        source = source.replace("$(env ", "$(")
        source = _bypass_wrapper_in_bash_completion(source)
    elif shell == "zsh":
        source = _bypass_wrapper_in_zsh_completion(source)
    elif shell == "fish":
        source = _bypass_wrapper_in_fish_completion(source)
    sys.stdout.write(source)
    sys.stdout.flush()


def main() -> None:
    cli(prog_name=CLI_NAME)


if __name__ == "__main__":
    main()
