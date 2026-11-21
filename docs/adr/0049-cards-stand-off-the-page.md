# ADR-0049: Cards stand off the page, in both palettes

- Status: Accepted
- Owner: Karl (implemented by Claude Code)
- Date: 2026-11-21
- Builds on: ADR-0048 (dark mode is a token swap), ADR-0006 (static, build-free UI)

## Context
ADR-0048 kept the light palette exactly as it was: white cards (`#fff`) on a near-white page
(`#f6f7f9`) with a pale border. On a bright screen the page read as one white sheet — it was
hard to see where one card ended and the next began, and the preset cards inside the Library
card had the same fill as the card around them.

## Decision
1. **The page is the darker surface and cards are lighter, in both palettes.** Light: a grey-blue
   page (`--bg`) under off-white cards (`--card`), with a darker `--line`. Dark: the page went a
   step darker and the card border a step lighter. Nothing in light mode is pure white except
   the small raised surfaces below.
2. **Two new tokens, defined in all three palette blocks** (the ADR-0048 rule):
   `--shadow` (the drop shadow under the header, cards and preset cards) and `--raised`
   (the fill of a card that sits on another card — today the Library's preset cards).
3. The other light washes (`--soft`, `--panel`, `--accent-soft`, the warning and error washes)
   were darkened by the same step so they still read against the off-white card. `--muted` is
   darker so small labels keep their contrast.

## Consequences
- Light mode is visibly different from releases up to 0.3.1. The accent, ink and error colors
  are unchanged.
- `tests/test_ui_browser.py::test_cards_stand_off_the_page_in_both_themes` fails if the page,
  card and preset-card fills drift back together, or if the light page returns to near-white.
- Still one static file with no build step (ADR-0006).
