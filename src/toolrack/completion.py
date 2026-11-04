"""Shell-completion fast path that answers from the metadata cache.

Importing Click and building the dynamic command tree dominates the latency of
a <TAB> press. When the command-metadata cache is current, the answer can be
computed from the cached entries alone, using only the standard library.

Design rules:

* The fast path never *guesses*. Anything it does not model exactly (unknown
  tokens, option bundles, hidden alias groups, a stale or missing cache, ...)
  returns ``None`` and the request is handled by Click as before.
* Repository discovery and cache validation come from ``toolrack.runtime`` so
  they cannot drift from the CLI.
* ``tests/test_completion.py`` runs both implementations side by side and
  requires byte-identical output, so Click upgrades that change completion
  behaviour fail loudly instead of silently diverging.
"""

from __future__ import annotations

import os
import shlex
import sys
from collections.abc import Mapping

from toolrack import runtime

Completion = tuple[str, str, str]  # (type, value, help)

_MODES = {"bash_complete", "zsh_complete", "fish_complete"}
_HELP = ("--help", "Show this message and exit.")
_VERSION = ("--version", "Show the version and exit.")

# Mirrors the `core` group in toolrack.cli; tests compare it with the real
# Click group so the two cannot diverge unnoticed.
CORE_COMMANDS: dict[str, dict] = {
    "register": {
        "description": "Register a script as a CLI subcommand.",
        "args": [{"name": "script_path", "positional": True, "required": True}],
    },
    "auto-register": {
        "description": "Scan scripts/ for sidecar+script pairs and register unregistered ones.",
        "args": [
            {
                "name": "dry_run",
                "flag": True,
                "option": "--dry-run",
                "help": "Show what would be registered without writing.",
            }
        ],
    },
    "unregister": {
        "description": "Remove a script from the registry.",
        "args": [{"name": "script_path", "positional": True, "required": True}],
    },
    "reregister": {"description": "Re-validate all registered commands."},
    "refresh-cache": {"description": "Rebuild the command metadata cache next to the registry."},
    "list": {"description": "List all registered scripts."},
    "install-completion": {
        "description": "Print the shell completion script to stdout.",
        "args": [{"name": "shell", "positional": True, "choices": ["bash", "zsh", "fish"]}],
    },
}
CORE_GROUP_HELP = "Registry management and shell-completion commands."


# --------------------------------------------------------------------------
# Request parsing
# --------------------------------------------------------------------------


def _split_words(value: str) -> list[str]:
    lexer = shlex.shlex(value, posix=True)
    lexer.whitespace_split = True
    lexer.commenters = ""
    try:
        return list(lexer)
    except ValueError:
        return value.split()


def _request(shell: str, environ: Mapping[str, str]) -> tuple[list[str], str] | None:
    """Return (args, incomplete) the way Click's shell classes derive them."""
    try:
        words = _split_words(environ["COMP_WORDS"])
        if shell == "fish":
            incomplete = environ.get("COMP_CWORD", "")
            args = words[1:]
            if incomplete and args and args[-1] == incomplete:
                args.pop()
            return args, incomplete
        cword = int(environ["COMP_CWORD"])
    except (KeyError, ValueError):
        return None
    return words[1:cword], words[cword] if cword < len(words) else ""


# --------------------------------------------------------------------------
# Command tree
# --------------------------------------------------------------------------


def _short_help(text: object, limit: int = 45) -> str:
    """Equivalent of ``click.utils.make_default_short_help``."""
    if not isinstance(text, str):
        return ""
    paragraph_end = text.find("\n\n")
    if paragraph_end != -1:
        text = text[:paragraph_end]
    words = text.split()
    if not words:
        return ""
    if words[0] == "\b":
        words = words[1:]

    total = 0
    last = len(words) - 1
    index = 0
    for index, word in enumerate(words):
        total += len(word) + (index > 0)
        if total > limit:
            break
        if word[-1] == ".":
            return " ".join(words[: index + 1])
        if total == limit and index != last:
            break
    else:
        return " ".join(words)

    total += len("...")
    while index > 0:
        total -= len(words[index]) + (index > 0)
        if total <= limit:
            break
        index -= 1
    return " ".join(words[:index]) + "..."


def _new_node(help_text: str = "") -> dict:
    return {"help": help_text, "groups": {}, "commands": {}}


def _cli_name(name: str) -> str:
    return name.replace("_", "-")


def _group_help(entry_path: str, group_parts: list[str], depth: int) -> str:
    """Reproduce the help text cli._build_cli_tree gives a generated group."""
    name = group_parts[depth]
    if depth == 0:
        parts = entry_path.replace("\\", "/").split("/")
        if parts[0] == "scripts":
            parts = parts[1:]
        original = parts[0] if len(parts) > 1 else name
        folder = original if _cli_name(original) != name else name
        return f"Commands from scripts/{folder}/"
    return f"Commands from scripts/{'/'.join(group_parts[: depth + 1])}/"


def _build_tree(entries: list[dict]) -> dict | None:
    root = _new_node()
    root["groups"]["core"] = core = _new_node(CORE_GROUP_HELP)
    core["commands"].update(CORE_COMMANDS)

    for entry in entries:
        if not isinstance(entry, dict):
            return None
        group_parts = entry.get("group_parts")
        command_name = entry.get("cmd_name")
        sidecar = entry.get("sidecar")
        entry_path = entry.get("entry")
        if not (
            isinstance(group_parts, list)
            and all(isinstance(part, str) for part in group_parts)
            and isinstance(command_name, str)
            and isinstance(sidecar, dict)
            and isinstance(entry_path, str)
        ):
            return None

        node = root
        for depth, part in enumerate(group_parts):
            if part not in node["groups"]:
                node["groups"][part] = _new_node(_group_help(entry_path, group_parts, depth))
            node = node["groups"][part]
        node["commands"][command_name] = sidecar
    return root


# --------------------------------------------------------------------------
# Completion
# --------------------------------------------------------------------------


def _arg_specs(sidecar: dict) -> list[dict] | None:
    raw = sidecar.get("args") or []
    if not isinstance(raw, list):
        return None
    specs = [arg for arg in raw if isinstance(arg, dict) and isinstance(arg.get("name"), str)]
    return specs if len(specs) == len(raw) else None


def _option_name(arg: dict) -> str:
    return arg.get("option") or "--" + arg["name"].replace("_", "-")


def _value_completions(arg: dict, incomplete: str) -> list[Completion]:
    choices = arg.get("choices")
    if choices:
        return [("plain", str(c), "") for c in choices if str(c).startswith(incomplete)]
    if arg.get("type") == "path":
        return [("file", incomplete, "")]
    return []


def _command_completions(
    sidecar: dict, args: list[str], incomplete: str
) -> list[Completion] | None:
    specs = _arg_specs(sidecar)
    if specs is None:
        return None
    options = {_option_name(a): a for a in specs if not a.get("positional")}
    positionals = [a for a in specs if a.get("positional")]
    used: set[str] = set()
    positional_count = 0
    pending: dict | None = None
    parse_options = True

    for token in args:
        if pending is not None:
            pending = None
            continue
        if parse_options and token == "--":
            parse_options = False
        elif parse_options and token.startswith("-") and token != "-":
            name, separator, _value = token.partition("=")
            option = options.get(name)
            if option is None:
                return None  # --help, short-option bundles, unknown: let Click decide
            used.add(name)
            if not option.get("flag") and not separator:
                pending = option
        else:
            positional_count += 1

    if pending is not None:
        return _value_completions(pending, incomplete)

    if parse_options and incomplete.startswith("-"):
        name, separator, value = incomplete.partition("=")
        if separator:
            option = options.get(name)
            return None if option is None else _value_completions(option, value)
        matches = [
            ("plain", flag, str(spec.get("help") or ""))
            for flag, spec in options.items()
            if flag.startswith(incomplete)
            and (spec.get("multiple") or flag not in used)
        ]
        if _HELP[0].startswith(incomplete) and _HELP[0] not in used:
            matches.append(("plain", *_HELP))
        return matches

    if positional_count < len(positionals):
        return _value_completions(positionals[positional_count], incomplete)
    if positionals and positionals[-1].get("variadic"):
        return _value_completions(positionals[-1], incomplete)
    return []


def _node_completions(node: dict, incomplete: str, *, root: bool) -> list[Completion]:
    if incomplete.startswith("-"):
        flags = [_VERSION, _HELP] if root else [_HELP]
        return [("plain", *flag) for flag in flags if flag[0].startswith(incomplete)]

    items = [
        ("plain", name, _short_help(group["help"])) for name, group in node["groups"].items()
    ]
    items += [
        ("plain", name, _short_help(sidecar.get("description")))
        for name, sidecar in node["commands"].items()
    ]
    return sorted(
        (item for item in items if item[1].startswith(incomplete)), key=lambda item: item[1]
    )


def complete(entries: list[dict], args: list[str], incomplete: str) -> list[Completion] | None:
    """Return completions, or None when Click must answer instead."""
    node = _build_tree(entries)
    if node is None:
        return None

    root = True
    for index, token in enumerate(args):
        if token.startswith("-"):
            return None
        if token in node["groups"]:
            node = node["groups"][token]
            root = False
        elif token in node["commands"]:
            return _command_completions(node["commands"][token], args[index + 1 :], incomplete)
        else:
            return None  # hidden alias group, typo, ...
    return _node_completions(node, incomplete, root=root)


# --------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------


def _format(shell: str, item: Completion) -> str:
    kind, value, help_text = item
    if shell == "zsh":
        if help_text:
            value = value.replace(":", r"\:")
        return f"{kind}\n{value}\n{help_text or '_'}"
    if shell == "fish" and help_text:
        return f"{kind},{value}\t{help_text}"
    return f"{kind},{value}"


def try_fast_completion(environ: Mapping[str, str] = os.environ) -> bool:
    """Answer a completion request from the cache. Return False to defer to Click."""
    mode = environ.get(runtime.completion_var(runtime.cli_name(environ)))
    if mode not in _MODES:
        return False
    shell = mode.removesuffix("_complete")

    request = _request(shell, environ)
    if request is None:
        return False

    repo = runtime.resolve_repository(environ)
    try:
        entries = runtime.read_cache(runtime.read_registry(repo), repo)
    except (OSError, ValueError):
        return False
    if entries is None:
        return False

    items = complete(entries, *request)
    if items is None:
        return False

    if os.name == "nt" and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(newline="", errors="replace")
    sys.stdout.write("\n".join(_format(shell, item) for item in items) + "\n")
    sys.stdout.flush()
    return True
