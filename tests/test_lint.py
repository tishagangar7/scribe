"""DA-118 -- the determinism lint flags unsafe workflows and leaves safe ones alone.

Everything here checks source strings via `check_source`, not real files --
a deliberately unsafe workflow only needs to be valid Python to parse, it
never has to run.
"""

from __future__ import annotations

from scribe.lint import check_source


def test_datetime_now_is_flagged():
    src = """
from datetime import datetime

@workflow(name="w")
async def w(ctx):
    return datetime.now().hour
"""
    violations = check_source(src)
    assert len(violations) == 1
    assert "datetime.now()" in violations[0].message


def test_datetime_module_now_is_flagged():
    """`_base_name` walks past the inner `.datetime` to the `datetime` import."""
    src = """
import datetime

@workflow(name="w")
async def w(ctx):
    return datetime.datetime.now()
"""
    violations = check_source(src)
    assert len(violations) == 1
    assert "datetime.now()" in violations[0].message


def test_time_time_is_flagged():
    src = """
import time

@workflow(name="w")
async def w(ctx):
    return time.time()
"""
    violations = check_source(src)
    assert len(violations) == 1
    assert "time.time()" in violations[0].message


def test_random_attr_call_is_flagged():
    src = """
import random

@workflow(name="w")
async def w(ctx):
    return random.random()
"""
    violations = check_source(src)
    assert len(violations) == 1
    assert "random" in violations[0].message


def test_random_from_import_is_flagged():
    src = """
from random import random

@workflow(name="w")
async def w(ctx):
    return random()
"""
    violations = check_source(src)
    assert len(violations) == 1


def test_uuid4_is_flagged():
    src = """
import uuid

@workflow(name="w")
async def w(ctx):
    return str(uuid.uuid4())
"""
    violations = check_source(src)
    assert len(violations) == 1
    assert "uuid.uuid4()" in violations[0].message


def test_os_environ_is_flagged():
    src = """
import os

@workflow(name="w")
async def w(ctx):
    return os.environ["KEY"]
"""
    violations = check_source(src)
    assert len(violations) == 1
    assert "os.environ" in violations[0].message


def test_os_getenv_is_flagged():
    src = """
import os

@workflow(name="w")
async def w(ctx):
    return os.getenv("KEY")
"""
    violations = check_source(src)
    assert len(violations) == 1
    assert "os.getenv()" in violations[0].message


def test_aliased_import_is_still_caught():
    src = """
from datetime import datetime as dt

@workflow(name="w")
async def w(ctx):
    return dt.now()
"""
    violations = check_source(src)
    assert len(violations) == 1


def test_safe_workflow_using_ctx_helpers_is_clean():
    src = """
@workflow(name="w")
async def w(ctx):
    now = await ctx.now()
    r = await ctx.random()
    u = await ctx.uuid()
    return now.hour, r, str(u)
"""
    assert check_source(src) == []


def test_nondeterminism_outside_a_workflow_is_ignored():
    """The lint only inspects @workflow bodies -- plain helpers are exempt."""
    src = """
import random

def helper():
    return random.random()

@workflow(name="w")
async def w(ctx):
    return await ctx.step("s", lambda: helper())
"""
    assert check_source(src) == []


def test_multiple_violations_in_one_workflow_are_all_reported():
    src = """
import random
import uuid

@workflow(name="w")
async def w(ctx):
    a = random.random()
    b = uuid.uuid4()
    return a, b
"""
    violations = check_source(src)
    assert len(violations) == 2
