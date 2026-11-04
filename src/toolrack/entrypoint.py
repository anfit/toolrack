"""Process entry point with a lightweight shell-completion fast path."""

from __future__ import annotations

from toolrack.completion import try_fast_completion


def main() -> None:
    if try_fast_completion():
        return

    from toolrack.cli import main as click_main

    click_main()
