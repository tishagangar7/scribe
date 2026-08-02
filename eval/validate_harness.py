"""DA-302 -- prove the harness works end to end, on one real SWE-bench issue.

This validates the harness before any agent exists: check out a real repo,
apply the benchmark's own known-correct patch, run its tests, observe the
result flip from failing to passing.

Not a pytest test -- it needs live GitHub access and a real `docker build`
(to install the instance's pinned dependencies), neither of which belongs
in the fast, hermetic CI suite. Run it manually:

    uv run python -m eval.validate_harness

Instance choice: `pylint-dev__pylint-5859` -- one FAIL_TO_PASS test, a
tiny pure-Python dependency set (astroid + pytest, both pinned in the
repo's own requirements_test_min.txt), and no live network calls in its
test suite. (psf/requests, by contrast, hits real HTTP test servers from
its tests, which would conflict with the sandbox's network_mode="none".)
Generalizing instance selection to arbitrary Lite issues needs the real
SWE-bench per-repo install-command mapping -- out of scope for Week 1.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import docker

from agent.sandbox import run_in_sandbox
from eval.swebench import checkout_repo, get_instance, test_command

INSTANCE_ID = "pylint-dev__pylint-5859"
IMAGE_TAG = "scribe-eval-pylint-5859"

# Editable-installed against /repo -- the same path run_in_sandbox mounts
# the (patched) repo to at run time, so the install keeps resolving to
# whatever is currently mounted there.
#
# Deliberately not `pip install -r requirements_test_min.txt`: that file
# also lists `pytest-benchmark~=3.4`, whose latest matching release pulls
# in a `py` version that dropped `py.io.TerminalWriter`, which
# pytest-benchmark's own plugin still imports -- an unrelated benchmark
# plugin breaking collection for a test file that never uses it. Installing
# only what tests/checkers/unittest_misc.py actually needs sidesteps it.
DOCKERFILE = """\
FROM python:3.10-slim
COPY repo /repo
WORKDIR /repo
RUN pip install --no-cache-dir -e .[testutil] "astroid==2.9.3" "pytest~=7.0"
"""


def _build_image(repo_dir: Path) -> str:
    with tempfile.TemporaryDirectory() as build_ctx:
        ctx = Path(build_ctx)
        shutil.copytree(repo_dir, ctx / "repo")
        (ctx / "Dockerfile").write_text(DOCKERFILE)
        client = docker.from_env()
        client.images.build(path=str(ctx), tag=IMAGE_TAG, rm=True)
    return IMAGE_TAG


def main() -> None:
    instance = get_instance(INSTANCE_ID)
    print(f"validating harness on {instance.instance_id} ({instance.repo})")

    with tempfile.TemporaryDirectory() as tmp:
        repo_dir = Path(tmp) / "repo"
        print("cloning repo and checking out base commit...")
        checkout_repo(instance, repo_dir)

        print("building image (installs pinned deps, one-time network use)...")
        image = _build_image(repo_dir)

        cmd = test_command(instance)

        print("running FAIL_TO_PASS + PASS_TO_PASS before the fix...")
        before = run_in_sandbox(
            repo_dir, patch=instance.test_patch, test_command=cmd, image=image
        )
        print(f"  before: passed={before.passed}")

        print("applying the gold patch, running again...")
        after = run_in_sandbox(
            repo_dir,
            patch=instance.test_patch + instance.patch,
            test_command=cmd,
            image=image,
        )
        print(f"  after:  passed={after.passed}")

    if before.passed:
        print("\nunexpected: issue reproduced as already passing before the fix")
        print(before.stdout[-2000:])
    if not after.passed:
        print("\nunexpected: gold patch did not resolve the issue")
        print(after.stdout[-2000:])
        print(after.stderr[-2000:])

    assert not before.passed, "expected the issue to reproduce before the fix"
    assert after.passed, "expected the gold patch to resolve the issue"
    print("\nharness validated: issue reproduced pre-fix, resolved post-fix.")


if __name__ == "__main__":
    main()
