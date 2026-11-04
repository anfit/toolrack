"""Process entry point with lightweight fast paths for completion and --help."""

from __future__ import annotations

import sys

from toolrack.completion import try_fast_completion


def main() -> None:
    if try_fast_completion():
        return

    if "--help" in sys.argv[1:] or sys.argv[1:] == ["--version"]:
        from toolrack.helpfast import try_fast_help

        if try_fast_help(sys.argv[1:]):
            return

    from toolrack.cli import main as click_main

    click_main()
