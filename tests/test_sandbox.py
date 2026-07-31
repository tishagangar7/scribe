"""DA-202 -- Docker sandbox tests.

Trivial hand-built repos, not pytest-inside-the-container: the sandbox must
work against a stock image with nothing installed at run time (no network),
so pass/fail is just the test script's process exit code.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from agent.sandbox import PatchApplyError, run_in_sandbox


def _write(path: Path, text: str) -> None:
    path.write_text(text)


def test_passing_command_reports_passed(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _write(repo / "ok.py", "raise SystemExit(0)\n")

    result = run_in_sandbox(repo, patch=None, test_command="python ok.py")

    assert result.passed
    assert result.exit_code == 0
    assert not result.timed_out


def test_failing_command_reports_failed(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _write(repo / "bad.py", "raise AssertionError('boom')\n")

    result = run_in_sandbox(repo, patch=None, test_command="python bad.py")

    assert not result.passed
    assert result.exit_code != 0
    assert "AssertionError" in result.stderr
    assert "AssertionError" not in result.stdout


def test_patch_fixes_a_failing_check(tmp_path):
    """The actual use case: a buggy function, a patch, tests flip from red to green."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _write(repo / "calc.py", "def add(a, b):\n    return a - b\n")
    _write(
        repo / "check.py",
        "import sys\nfrom calc import add\nsys.exit(0 if add(2, 3) == 5 else 1)\n",
    )

    unpatched = run_in_sandbox(repo, patch=None, test_command="python check.py")
    assert not unpatched.passed

    patch = (
        "--- a/calc.py\n"
        "+++ b/calc.py\n"
        "@@ -1,2 +1,2 @@\n"
        " def add(a, b):\n"
        "-    return a - b\n"
        "+    return a + b\n"
    )
    patched = run_in_sandbox(repo, patch=patch, test_command="python check.py")
    assert patched.passed
    assert patched.exit_code == 0


def test_bad_patch_raises_before_any_container_runs(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _write(repo / "calc.py", "def add(a, b):\n    return a - b\n")

    bogus_patch = (
        "--- a/calc.py\n"
        "+++ b/calc.py\n"
        "@@ -1,2 +1,2 @@\n"
        " def add(a, b):\n"
        "-    return a * b\n"  # context line doesn't match calc.py -> rejected
        "+    return a + b\n"
    )
    with pytest.raises(PatchApplyError):
        run_in_sandbox(repo, patch=bogus_patch, test_command="python calc.py")


def test_network_is_disabled(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _write(
        repo / "net.py",
        "import socket, sys\n"
        "s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)\n"
        "s.settimeout(3)\n"
        "try:\n"
        "    s.connect(('8.8.8.8', 53))\n"
        "    sys.exit(1)\n"  # connection succeeding would mean network leaked in
        "except OSError:\n"
        "    sys.exit(0)\n",
    )

    result = run_in_sandbox(repo, patch=None, test_command="python net.py")

    assert result.passed, f"network reachable inside sandbox: {result.stderr}"


def test_wall_clock_timeout_kills_the_container(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _write(repo / "slow.py", "import time\ntime.sleep(30)\n")

    started = time.monotonic()
    result = run_in_sandbox(
        repo, patch=None, test_command="python slow.py", timeout_seconds=2
    )
    elapsed = time.monotonic() - started

    assert result.timed_out
    assert not result.passed
    assert elapsed < 15, "timeout did not actually kill the container promptly"
