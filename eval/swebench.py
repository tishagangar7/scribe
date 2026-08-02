"""DA-301 -- SWE-bench-lite dataset loader and repo checkout helpers.

Loads instance metadata from HuggingFace (`princeton-nlp/SWE-bench_Lite`)
and provides what every downstream consumer needs: a repo checked out at
the instance's base commit, and a test command built from the instance's
FAIL_TO_PASS / PASS_TO_PASS test node IDs.

Scope note: `test_command` assumes a pytest-collectible repo. That covers
most of Lite (astropy, flask, matplotlib, pytest, requests, scikit-learn,
seaborn, sphinx, xarray, pylint). django and sympy use their own test
runners and are not handled here.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from datasets import load_dataset
from pydantic import BaseModel

DATASET_NAME = "princeton-nlp/SWE-bench_Lite"


class SWEBenchInstance(BaseModel):
    """One SWE-bench-lite issue: a repo, a commit, and a known-correct fix."""

    instance_id: str
    repo: str  # e.g. "psf/requests"
    base_commit: str
    patch: str  # the gold fix
    test_patch: str  # adds/modifies the tests that prove it
    problem_statement: str
    fail_to_pass: list[str]
    pass_to_pass: list[str]
    version: str


def load_lite(split: str = "test") -> list[SWEBenchInstance]:
    """Pull SWE-bench-lite from HuggingFace and parse every row.

    Network access required. `split="test"` is the 300-issue eval set;
    `split="dev"` is a smaller 23-issue set for local iteration.
    """
    rows = load_dataset(DATASET_NAME, split=split)
    return [_parse_row(row) for row in rows]


def get_instance(instance_id: str, split: str = "test") -> SWEBenchInstance:
    """Fetch one instance by ID. Network access required (see `load_lite`)."""
    for inst in load_lite(split):
        if inst.instance_id == instance_id:
            return inst
    raise KeyError(f"no instance {instance_id!r} in {DATASET_NAME} split={split!r}")


def _parse_row(row: dict) -> SWEBenchInstance:
    return SWEBenchInstance(
        instance_id=row["instance_id"],
        repo=row["repo"],
        base_commit=row["base_commit"],
        patch=row["patch"],
        test_patch=row["test_patch"],
        problem_statement=row["problem_statement"],
        fail_to_pass=json.loads(row["FAIL_TO_PASS"]),
        pass_to_pass=json.loads(row["PASS_TO_PASS"]),
        version=row["version"],
    )


def checkout_repo(instance: SWEBenchInstance, dest: Path) -> Path:
    """Shallow-clone `instance.repo` and check out its base commit into `dest`.

    Network access required. `dest` must not already exist.
    """
    url = f"https://github.com/{instance.repo}.git"
    subprocess.run(["git", "clone", "--quiet", url, str(dest)], check=True)
    subprocess.run(
        ["git", "checkout", "--quiet", instance.base_commit], cwd=dest, check=True
    )
    return dest


def test_command(instance: SWEBenchInstance) -> str:
    """A pytest invocation covering every FAIL_TO_PASS and PASS_TO_PASS node ID.

    Assumes FAIL_TO_PASS/PASS_TO_PASS are pytest node IDs, true for the
    pytest-based repos in Lite (not django or sympy).
    """
    node_ids = instance.fail_to_pass + instance.pass_to_pass
    quoted = " ".join(f"'{n}'" for n in node_ids)
    return f"pytest -q {quoted}"
