# Path rules: updaters

An updater mutates one entity's `state` dict in place for the current tick:
`(ctx: EntityContext, state, params) -> None`. It powers stateful entities
(ADR-0009). Adding one is a single decorated function here — never touch
the engine (INV-4).

- Register with `@updater("id", example={...})`. Params come from the
  `UpdateSpec.params`. **The `example` is required in practice**: the UI fills
  a rule's params box from it (ADR-0021), so an updater without one is a rule
  a user can pick and then has no idea how to configure.
  `test_updater_examples.py` asserts every updater has one, and runs it — an
  example that no longer drives its updater fails the build.
- All entropy from `ctx.rng` / `ctx.faker` (INV-3). No `random` globals, no
  wall-clock time — an entity's evolution must be reproducible under a seed.
- Updaters are *stateless code over stateful data*: read/write the `state`
  dict, keep no module-level state. Cross-tick memory lives in `state` (e.g.
  `movement` stashes heading there), not in the updater.
- Initial state comes from the spec's `columns` (generated once per entity
  at tick 0). Updaters only run for ticks >= 1.
- Don't overload `GenContext` with per-entity fields — that's what
  `EntityContext` is for.
- Every new updater gets a docstring with a params example and a test that
  shows the state actually changing (and staying deterministic under seed).
