"""DA-201 -- Docker sandbox for running (possibly patched) repo tests.

Given a repo checkout, an optional unified-diff patch, and a shell command,
run the tests in an isolated container: no network, a memory cap, a
wall-clock timeout, destroyed afterwards regardless of outcome.

The patch is applied to a throwaway copy of the repo *before* the container
starts -- it is plain text rewriting files, not code execution. The only
thing that ever runs the patched code is the container itself, and that
container never touches the network.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

import docker
from docker.errors import APIError

DEFAULT_IMAGE = "python:3.12-slim"
DEFAULT_TIMEOUT_SECONDS = 60.0
DEFAULT_MEMORY_LIMIT = "512m"


class PatchApplyError(Exception):
    """The patch did not apply cleanly. No container is started in this case."""

    def __init__(self, stdout: str, stderr: str) -> None:
        super().__init__(f"patch failed to apply: {stderr.strip() or stdout.strip()}")
        self.stdout = stdout
        self.stderr = stderr


@dataclass(frozen=True)
class SandboxResult:
    """Outcome of one sandboxed test run."""

    passed: bool
    exit_code: int | None
    stdout: str
    stderr: str
    timed_out: bool = False


def run_in_sandbox(
    repo_path: str | Path,
    patch: str | None,
    test_command: str,
    *,
    image: str = DEFAULT_IMAGE,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    memory_limit: str = DEFAULT_MEMORY_LIMIT,
) -> SandboxResult:
    """Apply `patch` to a copy of `repo_path`, then run `test_command` in Docker.

    Args:
        repo_path: path to a clean checkout of the repo under test.
        patch: unified diff to apply, or None/"" to run the repo unmodified.
        test_command: shell command run as the container's entrypoint, e.g.
            "python check.py".
        image: Docker image the command runs in. It must already contain
            whatever `test_command` needs -- the container has no network,
            so nothing can be installed at run time.
        timeout_seconds: wall-clock budget for the test command.
        memory_limit: Docker `mem_limit` string, e.g. "512m".

    Returns:
        SandboxResult with pass/fail, exit code, captured stdout/stderr, and
        whether the run was killed for exceeding its timeout.

    Raises:
        PatchApplyError: the patch didn't apply. No container is started.
    """
    with tempfile.TemporaryDirectory(prefix="scribe-sandbox-") as tmp:
        work_dir = Path(tmp) / "repo"
        shutil.copytree(repo_path, work_dir)

        if patch:
            _apply_patch(work_dir, patch)

        return _run_container(
            work_dir,
            test_command,
            image=image,
            timeout_seconds=timeout_seconds,
            memory_limit=memory_limit,
        )


def _apply_patch(work_dir: Path, patch: str) -> None:
    proc = subprocess.run(
        ["patch", "-p1", "--forward"],
        cwd=work_dir,
        input=patch,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise PatchApplyError(proc.stdout, proc.stderr)


def _run_container(
    work_dir: Path,
    test_command: str,
    *,
    image: str,
    timeout_seconds: float,
    memory_limit: str,
) -> SandboxResult:
    client = docker.from_env()
    container = client.containers.run(
        image,
        command=["sh", "-c", test_command],
        working_dir="/repo",
        volumes={str(work_dir): {"bind": "/repo", "mode": "rw"}},
        # Run as the host uid/gid so anything the test command writes into
        # the bind mount (e.g. __pycache__) is owned by us, not root -- on a
        # real Linux bind mount, root-owned files left in the host tmp dir
        # can't be cleaned up by the (non-root) caller afterward.
        user=f"{os.getuid()}:{os.getgid()}",
        network_mode="none",
        mem_limit=memory_limit,
        detach=True,
    )
    timed_out = False
    exit_code: int | None
    try:
        try:
            result = container.wait(timeout=timeout_seconds)
            exit_code = result.get("StatusCode")
        except Exception:
            # container.wait()'s timeout is a client-side read timeout on
            # the docker daemon call -- it does not stop the container.
            # A stalled or runaway patch stops here rather than hanging.
            timed_out = True
            exit_code = None
            try:
                container.kill()
            except APIError:
                pass  # already exited between the timeout and this call

        stdout = container.logs(stdout=True, stderr=False).decode(
            "utf-8", errors="replace"
        )
        stderr = container.logs(stdout=False, stderr=True).decode(
            "utf-8", errors="replace"
        )
    finally:
        container.remove(force=True)

    return SandboxResult(
        passed=(not timed_out and exit_code == 0),
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        timed_out=timed_out,
    )
