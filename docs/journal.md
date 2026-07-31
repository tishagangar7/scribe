# Engineering journal

Daily standup format. Three lines minimum. This file becomes the writeup in
Sprint 6, so write for a reader, not just for yourself.

---

## Sprint 1 — Aug 3

**Done:** DA-101 models, DA-102 schema, DA-104 store, DA-106/107/108 context,
DA-109 decorators, DA-105 store tests, DA-110 crash recovery tests. 19 tests
green. Demo script showing crash/resume across separate processes.

**Learned:** The commit-before-return ordering in `ctx.step` is not a detail —
returning first would open a window where a paid-for step is lost. Wrote it up
as ADR 0002.

**Blockers:** none.

**Next:** DA-201 Docker sandbox, DA-301 SWE-bench loader.
