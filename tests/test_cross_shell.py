"""
Cross-shell execution harness for the toolrack CLI dispatcher.
Marked @pytest.mark.integration -- skipped by default.
Run explicitly:
    python -m pytest tests/test_cross_shell.py -m integration -s

What this verifies
------------------
For each shell context (cmd, Git Bash, Cygwin Bash):
  1. Tool listing   -- toolrack core list exits 0 and mentions registered scripts.
  2. Python probe   -- arg passthrough, env inheritance, stdin piping, cwd.
  3. Bash probe     -- same four properties via the Bash execution path.
  4. Exit code      -- dispatcher propagates non-zero exit codes unchanged.
  5. Completion     -- Click completion env var produces non-empty output.

Probe commands used
-------------------
  toolrack tests dummy echo-args      --alpha one --beta "spaced arg"
  toolrack tests dummy echo-args-sh   --alpha one --beta "spaced arg"

Each probe prints:
  ARGV=   received sys.argv[1:] / $@
  CWD=    working directory
  FOO=    value of $FOO env var
  STDIN=  piped stdin content

Shell conventions
-----------------
  cmd      : toolrack resolved via PATH
  Git Bash : toolrack resolved via PATH
  Cygwin   : toolrack resolved via PATH

Skipping
--------
Git Bash and Cygwin blocks skip with pytest.skip when the binary is absent.

Dummy scripts
-------------
The dummy probe scripts live in tests/dummy/ — inside the test tree, not
under scripts/, so they are never part of the main portfolio.
A session-scoped autouse fixture (register_dummy_scripts) registers them at
the start of the integration session and unregisters them at the end.
The CLI group path for the probes is:  toolrack tests dummy <cmd>
"""
import os
import subprocess
import sys
from pathlib import Path
import pytest

# ---------------------------------------------------------------------------
# Paths and CLI invocation
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parent.parent
_GIT_BASH = os.environ.get("GIT_BASH_EXE", r"C:\Program Files\Git\bin\bash.exe")
_CYGWIN_BASH = os.environ.get("CYGWIN_BASH_EXE", r"C:\cygwin64\bin\bash.exe")

# Python executable
_PYTHON_WIN  = sys.executable
_PYTHON_DIR  = str(Path(_PYTHON_WIN).parent)

# toolrack is on PATH via the installed console_script in .venv
_CLI = "toolrack"

_DUMMY_SCRIPTS = [
    "tests/dummy/echo_args.py",
    "tests/dummy/echo_args_sh.sh",
]

# ---------------------------------------------------------------------------
# Dummy-script lifecycle fixture
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session", autouse=True)
def register_dummy_scripts():
    """
    Register the dummy probe scripts into the live registry for the
    duration of the integration test session, then remove them.
    """
    env = _make_env()

    registered = []
    for rel in _DUMMY_SCRIPTS:
        result = subprocess.run(
            [_CLI, "core", "register", rel],
            cwd=ROOT, env=env, capture_output=True, text=True,
        )
        if result.returncode == 0:
            registered.append(rel)
        else:
            pytest.fail(
                f"Failed to register dummy script {rel}:\n"
                f"stdout: {result.stdout}\nstderr: {result.stderr}"
            )

    yield  # --- tests run here ---

    for rel in registered:
        subprocess.run(
            [_CLI, "core", "unregister", rel],
            cwd=ROOT, env=env, capture_output=True, text=True,
        )

# ---------------------------------------------------------------------------
# Environment helper
# ---------------------------------------------------------------------------
def _make_env(**extra) -> dict:
    """
    Return os.environ copy with:
      PATH        -- Python dir prepended so Bash shells find the right python
      FOO         -- sentinel value checked by probes
      EXIT_CODE   -- exit code probes will use (default 0)
    """
    env = os.environ.copy()
    path = env.get("PATH", "")
    env["PATH"] = _PYTHON_DIR + os.pathsep + path
    env.setdefault("FOO", "from_parent_env")
    env.setdefault("EXIT_CODE", "0")
    env.update(extra)
    return env

# ---------------------------------------------------------------------------
# Shell helpers
# ---------------------------------------------------------------------------
def _cmd_quote(command: str) -> str:
    if '"' not in command:
        return command
    return '"' + command.replace('"', '\\"') + '"'

def _posix_root(cygwin: bool = False) -> str:
    parts = ROOT.parts
    drive = parts[0].rstrip(":\\").lower()
    rest  = "/".join(parts[1:])
    if cygwin:
        return f"/cygdrive/{drive}/{rest}"
    return f"/{drive}/{rest}"

def run_cmd(command: str, env: dict) -> subprocess.CompletedProcess:
    print(f"\nCMD> {command}")
    p = subprocess.run(
        command,
        shell=True,
        cwd=ROOT,
        env=env,
        stdin=subprocess.DEVNULL,
        text=True,
        capture_output=True,
    )
    print(p.stdout, end="")
    if p.stderr:
        print("STDERR:", p.stderr, end="")
    print("RC=", p.returncode)
    return p

def run_bash(bash_exe: str, command: str, env: dict,
             cygwin: bool = False) -> subprocess.CompletedProcess:
    posix = _posix_root(cygwin=cygwin)
    full  = f"cd '{posix}' && {command}"
    print(f"\nBASH [{bash_exe}]> {command}")
    p = subprocess.run(
        [bash_exe, "-c", full],
        cwd=ROOT,
        env=env,
        stdin=subprocess.DEVNULL,
        text=True,
        capture_output=True,
    )
    print(p.stdout, end="")
    if p.stderr:
        print("STDERR:", p.stderr, end="")
    print("RC=", p.returncode)
    return p

# ---------------------------------------------------------------------------
# Skip guards
# ---------------------------------------------------------------------------
def _require_git_bash():
    if not os.path.isfile(_GIT_BASH):
        pytest.skip(f"Git Bash not found at {_GIT_BASH}")

def _require_cygwin():
    if not os.path.isfile(_CYGWIN_BASH):
        pytest.skip(f"Cygwin Bash not found at {_CYGWIN_BASH}")

# ---------------------------------------------------------------------------
# 1. Tool listing
# ---------------------------------------------------------------------------
@pytest.mark.integration
class TestListing:
    def test_list_cmd(self):
        p = run_cmd(f"{_CLI} core list", _make_env())
        assert p.returncode == 0, f"RC={p.returncode}\n{p.stderr}"
        assert "echo_args" in p.stdout

    def test_list_git_bash(self):
        _require_git_bash()
        p = run_bash(_GIT_BASH, f"{_CLI} core list", _make_env())
        assert p.returncode == 0, f"RC={p.returncode}\n{p.stderr}"
        assert "echo_args" in p.stdout

    def test_list_cygwin(self):
        _require_cygwin()
        p = run_bash(_CYGWIN_BASH, f"{_CLI} core list", _make_env(), cygwin=True)
        assert p.returncode == 0, f"RC={p.returncode}\n{p.stderr}"
        assert "echo_args" in p.stdout

# ---------------------------------------------------------------------------
# 2. Python probe
# ---------------------------------------------------------------------------
@pytest.mark.integration
class TestPythonProbe:
    _PROBE = f'{_CLI} tests dummy echo-args --alpha one --beta "spaced arg"'

    def _check(self, p: subprocess.CompletedProcess):
        assert p.returncode == 0, (
            f"RC={p.returncode}\nstdout: {p.stdout}\nstderr: {p.stderr}"
        )
        assert "--alpha"         in p.stdout
        assert "one"             in p.stdout
        assert "--beta"          in p.stdout
        assert "spaced arg"      in p.stdout
        assert "from_parent_env" in p.stdout
        assert "STDIN="          in p.stdout
        assert "hello"           in p.stdout
        assert "toolrack"        in p.stdout

    def test_cmd(self):
        self._check(run_cmd(f'echo hello | {self._PROBE}', _make_env()))

    def test_git_bash(self):
        _require_git_bash()
        self._check(run_bash(_GIT_BASH, f'echo hello | {self._PROBE}', _make_env()))

    def test_cygwin(self):
        _require_cygwin()
        self._check(run_bash(_CYGWIN_BASH, f'echo hello | {self._PROBE}',
                              _make_env(), cygwin=True))

# ---------------------------------------------------------------------------
# 3. Bash probe
# ---------------------------------------------------------------------------
@pytest.mark.integration
class TestBashProbe:
    _PROBE = f'{_CLI} tests dummy echo-args-sh --alpha one --beta "spaced arg"'

    def _check(self, p: subprocess.CompletedProcess):
        assert p.returncode == 0, (
            f"RC={p.returncode}\nstdout: {p.stdout}\nstderr: {p.stderr}"
        )
        assert "one"             in p.stdout
        assert "spaced"          in p.stdout
        assert "from_parent_env" in p.stdout
        assert "STDIN="          in p.stdout
        assert "hello"           in p.stdout
        assert "toolrack"        in p.stdout

    def test_cmd(self):
        self._check(run_cmd(f'echo hello | {self._PROBE}', _make_env()))

    def test_git_bash(self):
        _require_git_bash()
        self._check(run_bash(_GIT_BASH, f'echo hello | {self._PROBE}', _make_env()))

    def test_cygwin(self):
        _require_cygwin()
        self._check(run_bash(_CYGWIN_BASH, f'echo hello | {self._PROBE}',
                              _make_env(), cygwin=True))

# ---------------------------------------------------------------------------
# 4. Exit code propagation
# ---------------------------------------------------------------------------
@pytest.mark.integration
class TestExitCode:
    def test_python_probe_cmd(self):
        p = run_cmd(f"{_CLI} tests dummy echo-args", _make_env(EXIT_CODE="7"))
        assert p.returncode == 7

    def test_python_probe_git_bash(self):
        _require_git_bash()
        p = run_bash(_GIT_BASH, f"{_CLI} tests dummy echo-args", _make_env(EXIT_CODE="7"))
        assert p.returncode == 7

    def test_python_probe_cygwin(self):
        _require_cygwin()
        p = run_bash(_CYGWIN_BASH, f"{_CLI} tests dummy echo-args",
                     _make_env(EXIT_CODE="7"), cygwin=True)
        assert p.returncode == 7

    def test_bash_probe_git_bash(self):
        _require_git_bash()
        p = run_bash(_GIT_BASH, f"{_CLI} tests dummy echo-args-sh",
                     _make_env(EXIT_CODE="7"))
        assert p.returncode == 7

    def test_bash_probe_cygwin(self):
        _require_cygwin()
        p = run_bash(_CYGWIN_BASH, f"{_CLI} tests dummy echo-args-sh",
                     _make_env(EXIT_CODE="7"), cygwin=True)
        assert p.returncode == 7

# ---------------------------------------------------------------------------
# 5. Completion smoke test
# ---------------------------------------------------------------------------
@pytest.mark.integration
class TestCompletion:
    def _check(self, p: subprocess.CompletedProcess, label: str):
        assert p.returncode == 0, (
            f"[{label}] install-completion exited {p.returncode}\n"
            f"stdout: {p.stdout[:300]}\nstderr: {p.stderr[:300]}"
        )
        assert p.stdout.strip()
        assert "_TOOLRACK_COMPLETE" in p.stdout or "complete" in p.stdout or "compdef" in p.stdout

    def test_completion_cmd(self):
        p = run_cmd(f"{_CLI} core install-completion bash", _make_env())
        self._check(p, "cmd")

    def test_completion_git_bash(self):
        _require_git_bash()
        p = run_bash(_GIT_BASH, f"{_CLI} core install-completion bash", _make_env())
        self._check(p, "git_bash")
