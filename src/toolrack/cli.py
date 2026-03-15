#!/usr/bin/env python3
"""toolrack - typed dispatcher for a user-owned scripts repository."""

from __future__ import annotations

import configparser
import json
import ntpath
import os
import subprocess
import sys
from dataclasses import dataclass

import click


def _default_cli_name() -> str:
    prog = os.path.basename(sys.argv[0] or "").strip()
    if prog:
        prog = os.path.splitext(prog)[0]
        if prog not in {"", "__main__", "-m"}:
            return prog
    return "toolrack"


CLI_NAME = os.environ.get("TOOLRACK_CLI_NAME") or _default_cli_name()
COMPLETION_VAR = "_" + CLI_NAME.replace("-", "_").upper() + "_COMPLETE"
DEFAULT_REGISTRY_BASENAME = os.environ.get("TOOLRACK_REGISTRY_BASENAME") or ".toolrack"
# TODO(#12): Move environment-derived settings into an explicit runtime config object.
# Import-time globals make packaging harder, complicate tests, and will get in
# the way once toolrack is used as an installed library as well as a checkout.


def _is_windows() -> bool:
    return os.name == "nt"


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


def _registry_candidates() -> list[str]:
    candidates = [
        os.environ.get("TOOLRACK_REGISTRY_FILE"),
        DEFAULT_REGISTRY_BASENAME,
        ".attic",
    ]
    return [candidate for candidate in candidates if candidate]


def _normalize_env_path(value: str | None) -> str | None:
    """Map Git Bash / Cygwin Windows mount paths back to native Windows paths."""
    if not value or not _is_windows():
        return value

    normalized = value.replace("\\", "/")
    if normalized.startswith("/cygdrive/") and len(normalized) > len("/cygdrive/x/"):
        drive = normalized[len("/cygdrive/")]
        remainder = normalized[len("/cygdrive/x") :]
        return ntpath.normpath(f"{drive.upper()}:{remainder}")

    if normalized.startswith("/") and len(normalized) > 3 and normalized[2] == "/":
        drive = normalized[1]
        if drive.isalpha():
            remainder = normalized[2:]
            return ntpath.normpath(f"{drive.upper()}:{remainder}")

    return value


def _find_repo_root() -> str:
    """Walk up from cwd until we find the registry file or .git."""
    here = os.path.abspath(os.getcwd())
    candidate = here
    while True:
        for registry_name in _registry_candidates():
            if os.path.isabs(registry_name):
                continue
            if os.path.isfile(os.path.join(candidate, registry_name)):
                return candidate
        if os.path.isdir(os.path.join(candidate, ".git")):
            return candidate
        parent = os.path.dirname(candidate)
        if parent == candidate:
            return here
        candidate = parent


REPO_ROOT = _normalize_env_path(os.environ.get("TOOLRACK_REPO_ROOT")) or _find_repo_root()
SCRIPTS_ROOT = _normalize_env_path(os.environ.get("TOOLRACK_SCRIPTS_ROOT")) or os.path.join(
    REPO_ROOT, "scripts"
)
REGISTRY_FILE = (
    _normalize_env_path(os.environ.get("TOOLRACK_REGISTRY_FILE"))
    or os.path.join(REPO_ROOT, DEFAULT_REGISTRY_BASENAME)
)
CACHE_FILE = _normalize_env_path(os.environ.get("TOOLRACK_CACHE_FILE")) or (REGISTRY_FILE + ".cache.json")
ALIASES_FILE = _normalize_env_path(os.environ.get("TOOLRACK_ALIASES_FILE")) or os.path.join(
    REPO_ROOT, "aliases.cfg"
)


@dataclass(frozen=True)
class RepositoryContext:
    repo_root: str
    scripts_root: str
    registry_file: str
    cache_file: str
    aliases_file: str


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
    repo = repo or _current_repo()
    if not os.path.isfile(repo.registry_file):
        return []
    with open(repo.registry_file, encoding="utf-8") as handle:
        return [
            line.strip()
            for line in handle
            if line.strip() and not line.strip().startswith("#")
        ]


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
    repo = repo or _current_repo()
    return os.path.normpath(os.path.join(repo.repo_root, registry_entry))


def _file_state(path: str) -> dict[str, int | bool]:
    try:
        stat = os.stat(path)
    except FileNotFoundError:
        return {"exists": False, "size": 0, "mtime_ns": 0}
    return {"exists": True, "size": stat.st_size, "mtime_ns": stat.st_mtime_ns}


def _cache_signature(registry: list[str], repo: RepositoryContext | None = None) -> str:
    repo = repo or _current_repo()
    # TODO(#3): Promote cached entry payloads to typed models instead of raw dicts.
    # The current JSON contract is convenient but fragile: field names are
    # unchecked, easy to drift, and spread across multiple helpers.
    payload = {
        "repo_root": repo.repo_root,
        "scripts_root": repo.scripts_root,
        "registry_file": repo.registry_file,
        "aliases_file": repo.aliases_file,
        "registry": registry,
        "registry_state": _file_state(repo.registry_file),
        "aliases_state": _file_state(repo.aliases_file),
        "entries": [
            {
                "registry_entry": entry,
                "script_state": _file_state(_abs_script_path(entry, repo)),
                "sidecar_state": _file_state(_sidecar_path(_abs_script_path(entry, repo))),
            }
            for entry in registry
        ],
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _read_cache(registry: list[str], repo: RepositoryContext | None = None) -> list[dict] | None:
    repo = repo or _current_repo()
    if not os.path.isfile(repo.cache_file):
        return None
    try:
        with open(repo.cache_file, encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None
    if payload.get("signature") != _cache_signature(registry, repo):
        return None
    entries = payload.get("entries")
    if not isinstance(entries, list):
        return None
    return entries


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


def _sidecar_path(script_path: str) -> str:
    return os.path.splitext(script_path)[0] + ".yml"


def _generate_sidecar(script_path: str) -> dict:
    import yaml

    name = os.path.basename(script_path)
    sidecar = {
        "# autogenerated": (
            f"This sidecar was created automatically by `{CLI_NAME} core register`.\n"
            "Arguments are passed through verbatim. Replace it with a typed sidecar."
        ),
        "description": f"Run {name} (autogenerated; edit to add proper description).",
        "args": [
            {
                "name": "args",
                "positional": True,
                "variadic": True,
                "required": False,
                "help": "All arguments passed through verbatim to the script.",
            }
        ],
    }
    with open(_sidecar_path(script_path), "w", encoding="utf-8") as handle:
        yaml.dump(sidecar, handle, default_flow_style=False, allow_unicode=True, sort_keys=False)
    return sidecar


def _load_sidecar(script_path: str) -> dict | None:
    yml_path = _sidecar_path(script_path)
    if not os.path.isfile(yml_path):
        return None
    import yaml

    with open(yml_path, encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise RuntimeError(f"{yml_path}: sidecar must be a YAML mapping, got {type(data).__name__}")
    return data


def _validate_sidecar(sidecar: dict, script_path: str) -> None:
    ext = os.path.splitext(script_path)[1].lower()
    args = sidecar.get("args") or []
    name = os.path.basename(script_path)

    if ext == ".sql" and args:
        raise RuntimeError(f"{name}: SQL scripts must not declare 'args'.")

    seen_names: set[str] = set()
    variadic_seen = False
    for arg in args:
        arg_name = arg.get("name", "")
        if arg_name in seen_names:
            raise RuntimeError(f"{name}: duplicate arg name '{arg_name}'.")
        seen_names.add(arg_name)
        if variadic_seen:
            raise RuntimeError(f"{name}: '{arg_name}' appears after variadic; variadic must be last.")
        if arg.get("variadic"):
            if not arg.get("positional"):
                raise RuntimeError(f"{name}: '{arg_name}' is variadic but not positional.")
            variadic_seen = True
        if arg.get("multiple") and arg.get("positional"):
            raise RuntimeError(f"{name}: '{arg_name}' cannot be both multiple and positional.")
        if arg.get("flag") and arg.get("type") not in (None, "bool"):
            raise RuntimeError(f"{name}: '{arg_name}' is a flag but declares type '{arg['type']}'.")


def _to_bash_path(windows_path: str, bash_exe: str = "") -> str:
    # TODO(#5): Isolate shell/path translation behind a shell adapter layer.
    # Windows, Git Bash, and Cygwin compatibility logic is mixed into the core
    # dispatcher, which makes the module harder to reason about than it should be.
    if not _is_windows():
        return windows_path

    cygpath_candidates: list[str] = []
    if bash_exe:
        bash_dir = os.path.dirname(os.path.abspath(bash_exe))
        same_dir = os.path.join(bash_dir, "cygpath.exe")
        parent_usr_bin = os.path.join(os.path.dirname(bash_dir), "usr", "bin", "cygpath.exe")
        for candidate in (same_dir, parent_usr_bin):
            if os.path.isfile(candidate):
                cygpath_candidates.append(candidate)
    cygpath_candidates.append("cygpath")

    for cygpath_exe in cygpath_candidates:
        try:
            result = subprocess.run(
                [cygpath_exe, "-u", windows_path],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                return result.stdout.strip()
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue

    path = windows_path.replace("\\", "/")
    if len(path) >= 2 and path[1] == ":":
        path = "/" + path[0].lower() + path[2:]
    return path


def _resolve_bash_exe() -> str:
    if not _is_windows():
        return "bash"

    cygwin_bash = r"C:\cygwin64\bin\bash.exe"
    git_bash = r"C:\Program Files\Git\bin\bash.exe"

    if not os.environ.get("MSYSTEM"):
        path_dirs = os.environ.get("PATH", "").split(os.pathsep)
        cygwin_bin = r"C:\cygwin64\bin"
        gitbash_bin = r"C:\Program Files\Git\bin"

        def _norm(value: str) -> str:
            return os.path.normcase(os.path.normpath(value))

        normed = [_norm(entry) for entry in path_dirs]
        cygwin_idx = next((idx for idx, entry in enumerate(normed) if entry == _norm(cygwin_bin)), 999)
        gitbash_idx = next((idx for idx, entry in enumerate(normed) if entry == _norm(gitbash_bin)), 999)
        if cygwin_idx < gitbash_idx and os.path.isfile(cygwin_bash):
            return cygwin_bash

    if os.environ.get("MSYSTEM") and os.path.isfile(git_bash):
        return git_bash

    return "bash"


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


def _command_callback(spec: CommandSpec):
    def callback(**kwargs):
        # TODO(#7): Return subprocess exit codes instead of calling sys.exit from
        # nested callbacks. This works for the current CLI-only model, but it
        # makes embedding or reusing the dispatcher from Python awkward.
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
                    cmd.extend(str(item) for item in value)
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
        sys.exit(result.returncode)

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
@click.version_option(prog_name=CLI_NAME)
def cli():
    """Typed dispatcher for a user-owned scripts repository."""


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
    sys.stdout.write(source)
    sys.stdout.flush()


def main() -> None:
    cli(prog_name=CLI_NAME)


if __name__ == "__main__":
    main()
