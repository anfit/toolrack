"""pytest fixtures for testing a toolrack scripts repository.

Registered through the ``pytest11`` entry point, so installing ``toolrack-cli``
makes these fixtures available in any project's tests:

``toolrack_project``
    The real repository around the test run (found by walking up from the
    current directory, like the CLI does). Use it to validate and smoke-test
    your actual scripts, e.g. ``toolrack_project.validate(strict=True)``.

``toolrack_repo``
    A fresh, empty temporary repository for tests that need controlled
    scripts and sidecars.

Both return :class:`toolrack.testing.ToolrackRepo`, which runs the real CLI in
a subprocess.

``toolrack_require_bash``
    Request it (or use ``@pytest.mark.usefixtures``) in tests of ``.sh`` /
    ``.bash`` scripts: skips the test when no working Bash is available.

``toolrack_require_posix``
    Skips on Windows; for tests that use ``stub_executable`` (for example a
    fake ``psql`` to test ``.sql`` scripts).
"""

from __future__ import annotations

import os

import pytest

from toolrack.testing import ToolrackRepo, bash_available


@pytest.fixture()
def toolrack_project(request) -> ToolrackRepo:
    return ToolrackRepo.discover(request.config.rootpath)


@pytest.fixture()
def toolrack_repo(tmp_path) -> ToolrackRepo:
    return ToolrackRepo.create(tmp_path / "repo")


@pytest.fixture(scope="session")
def toolrack_require_bash() -> None:
    if not bash_available():
        pytest.skip("no working bash available")


@pytest.fixture()
def toolrack_require_posix() -> None:
    if os.name == "nt":
        pytest.skip("requires a POSIX host")
