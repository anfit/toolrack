"""`--help` fast path that renders from the metadata cache without importing Click.

Same contract as :mod:`toolrack.completion`: it never guesses. It handles only
help it can reproduce exactly (``tests/test_helpfast.py`` compares it with
Click's own output) and returns ``None`` for everything else, which Click then
answers as before. Not modelled, so deferred: text that would need wrapping,
epilogs and environment sections, unusual option spellings or defaults,
choice-typed positionals, non-ASCII text, and ``core`` subcommand help.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping

from toolrack import runtime
from toolrack.completion import _build_tree, _short_help

ROOT_HELP = "Typed dispatcher for a user-owned scripts repository."
_COL_MAX = 30
_COL_SPACING = 2
_METAVARS = {"int": "INTEGER", "float": "FLOAT", "bool": "BOOLEAN", "path": "PATH"}


class _Unsupported(Exception):
    """Raised internally when a request is outside what is modelled."""


def _columns() -> int:
    """Terminal width the way ``shutil.get_terminal_size`` decides it."""
    try:
        columns = int(os.environ["COLUMNS"])
    except (KeyError, ValueError):
        columns = 0
    if columns <= 0:
        try:
            columns = os.get_terminal_size(sys.__stdout__.fileno()).columns
        except (AttributeError, OSError, ValueError):
            columns = 0
    return columns if columns > 0 else 80


def _width() -> int:
    return max(min(_columns(), 80) - 2, 50)


def _plain(text: object) -> str:
    """Text that Click would print verbatim on one line; anything else is unsupported."""
    if not isinstance(text, str):
        raise _Unsupported
    if not text.isascii() or "\x1b" in text or "\b" in text or "\f" in text:
        raise _Unsupported
    if any(ch in text for ch in "\n\r\t") or "  " in text:
        raise _Unsupported
    return text.strip()


def _text_block(text: str, width: int) -> str:
    line = "  " + text
    if len(line) > width:
        raise _Unsupported
    return line + "\n"


def _definition_list(rows: list[tuple[str, str]], width: int) -> str:
    """Click's ``HelpFormatter.write_dl`` for rows that need no wrapping."""
    first_col = min(max(len(first) for first, _ in rows), _COL_MAX) + _COL_SPACING
    out = []
    for first, second in rows:
        line = "  " + first
        if second:
            if len(first) > first_col - _COL_SPACING:
                raise _Unsupported  # help would start on the next line
            if len(second) > max(width - first_col - 2, 10):
                raise _Unsupported  # help would wrap
            line += " " * (first_col - len(first)) + second
        out.append(line + "\n")
    return "".join(out)


def _usage_line(prog: str, pieces: str, width: int) -> str:
    """The `Usage:` line, if Click would print it on one unwrapped line."""
    prefix = f"Usage: {prog} "
    # Click moves the arguments to the next line when fewer than 20 columns
    # remain after the prefix, and wraps them when they overflow.
    if width < len(prefix) + 20 or len(prefix + pieces) > width:
        raise _Unsupported
    return prefix + pieces + "\n"


def _section(title: str, body: str) -> str:
    return f"\n{title}:\n{body}"


def _group_help(prog: str, help_text: str, node: dict, *, root: bool) -> str:
    width = _width()
    names = sorted([*node["groups"], *node["commands"]])
    limit = width - 6 - max(len(name) for name in names)
    rows = []
    for name in names:
        text = node["groups"][name]["help"] if name in node["groups"] else node["commands"][name].get("description")
        rows.append((name, _plain(_short_help(text, limit))))

    options = [("--version", "Show the version and exit.")] if root else []
    options.append(("--help", "Show this message and exit."))

    out = _usage_line(prog, "[OPTIONS] COMMAND [ARGS]...", width)
    text = _plain(help_text)
    if text:
        out += "\n" + _text_block(text, width)
    out += _section("Options", _definition_list(options, width))
    out += _section("Commands", _definition_list(rows, width))
    return out


def _option_metavar(arg: dict) -> str:
    choices = arg.get("choices")
    if choices:
        if not all(isinstance(c, (str, int)) and not isinstance(c, bool) for c in choices):
            raise _Unsupported
        return "[" + "|".join(str(c) for c in choices) + "]"
    return _METAVARS.get(arg.get("type"), "TEXT")


def _default_text(arg: dict) -> str | None:
    default = arg.get("default")
    if default is None:
        return None
    if isinstance(default, bool) or not isinstance(default, (str, int)) or default == "":
        raise _Unsupported
    return _plain(str(default))


def _command_help(prog: str, sidecar: dict) -> str:
    width = _width()
    raw_args = sidecar.get("args") or []
    if not isinstance(raw_args, list) or sidecar.get("env") or sidecar.get("epilog"):
        raise _Unsupported

    usage_pieces = ["[OPTIONS]"]
    rows: list[tuple[str, str]] = []
    for arg in raw_args:
        if not isinstance(arg, dict) or not isinstance(arg.get("name"), str):
            raise _Unsupported
        name = arg["name"]
        if arg.get("positional"):
            if arg.get("choices"):
                raise _Unsupported
            piece = name.upper()
            if not arg.get("required", True):
                piece = f"[{piece}]"
            if arg.get("variadic"):
                piece += "..."
            usage_pieces.append(piece)
            continue

        flag = arg.get("option") or "--" + name.replace("_", "-")
        if not isinstance(flag, str) or not flag.startswith("--") or not flag[2:].replace("-", "").isalnum():
            raise _Unsupported
        help_text = _plain(arg.get("help") or "")
        if arg.get("flag"):
            rows.append((flag, help_text))
            continue

        required = bool(arg.get("required"))
        default = _default_text(arg)
        if required and default is not None:
            raise _Unsupported
        extras = []
        if default is not None:
            extras.append(f"default: {default}")
        if required:
            extras.append("required")
        if extras:
            extra = "; ".join(extras)
            help_text = f"{help_text}  [{extra}]" if help_text else f"[{extra}]"
        rows.append((f"{flag} {_option_metavar(arg)}", help_text))
    rows.append(("--help", "Show this message and exit."))

    out = _usage_line(prog, " ".join(usage_pieces), width)
    description = _plain(sidecar.get("description") or "")
    if description:
        out += "\n" + _text_block(description, width)
    out += _section("Options", _definition_list(rows, width))
    return out


def render(entries: list[dict], prog: str, path: list[str]) -> str | None:
    """Help text for ``path`` (group or command names), or None to defer to Click."""
    node = _build_tree(entries)
    if node is None:
        return None
    try:
        trail = [prog]
        root = True
        for index, token in enumerate(path):
            if token in node["groups"]:
                node = node["groups"][token]
                trail.append(token)
                root = False
            elif token in node["commands"] and index == len(path) - 1 and trail[1:2] != ["core"]:
                return _command_help(" ".join([*trail, token]), node["commands"][token])
            else:
                return None
        help_text = ROOT_HELP if root else node["help"]
        return _group_help(" ".join(trail), help_text, node, root=root)
    except _Unsupported:
        return None


def try_fast_help(argv: list[str], environ: Mapping[str, str] = os.environ) -> bool:
    """Print help (or the version) from the cache. Return False to let Click answer."""
    if environ.get("TOOLRACK_NO_FAST_HELP") or not argv:
        return False
    if argv == ["--version"]:
        from toolrack import __version__

        sys.stdout.write(f"{runtime.cli_name(environ)}, version {__version__}\n")
        sys.stdout.flush()
        return True
    if argv[-1] != "--help":
        return False
    path = argv[:-1]
    if any(token.startswith("-") for token in path):
        return False

    repo = runtime.resolve_repository(environ)
    try:
        entries = runtime.read_cache(runtime.read_registry(repo), repo)
    except (OSError, ValueError):
        return False
    if entries is None:
        return False

    text = render(entries, runtime.cli_name(environ), path)
    if text is None:
        return False
    sys.stdout.write(text)
    sys.stdout.flush()
    return True
