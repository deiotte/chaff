# Path rules: generators

Adding a generator = one decorated function in this package. Never touch
the engine to add one (INV-4).

- All entropy from `ctx.rng` / `ctx.faker` (INV-3). If you import `random`
  at module top-level for anything but typing, you're about to break
  determinism.
- Generators are *semantic* ("full_name", "pattern"), not physical types
  ("varchar"). Physical typing is the encoder's job.
- Params get defaults so a bare `{"generator": "x"}` works — office Joe
  won't read a schema reference.
- Every new generator gets: a docstring with a params example, and a shape
  test in `tests/`.
- Phase 3 stateful entities will add an `EntityContext` alongside
  `GenContext`; don't overload `GenContext` with per-entity state.
