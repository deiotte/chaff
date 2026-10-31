# ADR-0048: Dark mode is a token swap that follows the OS

- Status: Accepted
- Owner: Karl (implemented by Claude Code)
- Date: 2026-10-31
- Builds on: ADR-0006 (static, build-free UI), ADR-0022 (browser-driven UI tests)

## Context
The UI was light-only, and people asked for a dark version because the bright page hurts to look
at for long. Its styles mixed a few CSS variables with about twenty hard-coded hex colors
(button and tab greys, pill and badge washes, warning banners, panel backgrounds, the preview's
`null` marker, and white form fields). A dark theme built on top of that would need a dark
override for every one of them, and a color added later would silently stay light.

## Decision
1. **Every UI color is a token on `:root`.** No rule in `api/static/index.html` names a hex
   value. The light palette is the old colors, mapped one for one, so light mode is unchanged.
2. **A dark palette redefines the same tokens.** It applies under
   `@media (prefers-color-scheme: dark)` (unless the user picked light) and under
   `:root[data-theme="dark"]`. `color-scheme` is set with it, so native controls, scrollbars and
   form fields match. The accent becomes a lighter green with dark text on it, and errors become
   a lighter red, so both keep readable contrast on dark cards.
3. **Auto is the default; a header button overrides it.** "Theme: Auto → Dark → Light" cycles,
   and the choice is saved in this browser's `localStorage` (`chaff_theme`; Auto removes the
   key). A tiny inline script in `<head>` applies the saved choice before first paint, so there
   is no white flash. Every storage access is guarded: with storage blocked the page still
   themes, it just won't remember.
4. Still one static file with no build step and no dependency (ADR-0006).

## Consequences
- A new color means a new token, defined in all three palette blocks (light, OS-dark,
  forced-dark). The comment at the top of the `<style>` block says so.
- The dark palette is defined twice (once for the OS media query, once for the forced
  attribute). CSS has no way to share one block between a media query and a selector without
  a build step, and ADR-0006 rules out a build step.
- `tests/test_ui_browser.py` checks the OS-following default, the override, that the override is
  remembered across a reload, and that a forced Light beats a dark OS.
- The `/licenses` page was already dark and is unchanged.
