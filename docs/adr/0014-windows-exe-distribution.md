# ADR-0014: Windows one-click .exe distribution

- Status: Accepted
- Owner: Karl (implemented by Claude Code)
- Date: 2026-07-11
- Amends: ADR-0005 (Docker-first distribution)

## Context
ADR-0005 made "pull from GitHub and build with Docker" the whole install
story. That's right for engineers, but a large audience — Windows users who
have never opened a terminal and can't install Docker — is shut out. The D4O
north star is an office Joe building a dataset without an engineer; for those
users the install *is* the wall. They need a file they can double-click.

## Decision
Ship a **single-file Windows executable, `chaff.exe`**, built with PyInstaller
(onefile) on a `windows-latest` GitHub Actions runner and attached to
releases. It is an **additional** channel — Docker remains the primary,
cross-platform path. No engine or spec change: the exe just launches the same
API that produces specs for the engine (INV-1..5 untouched).

- **Launcher** (`packaging/chaff_desktop.py`): the onefile entry point. It
  points `CHAFF_PRESETS_DIR` at the bundled `examples` and `CHAFF_LIBRARY_DIR`
  at a per-user writable dir (`%LOCALAPPDATA%\chaff\spec-library`), picks a
  free localhost port (8000, else OS-assigned), starts uvicorn
  (`loop=asyncio, http=h11` — pure Python, no native `uvloop`/`httptools`),
  and opens the default browser once the port accepts. Close the console
  window to stop.
- **Static UI when frozen**: `api/main.py` resolves the UI dir under
  `sys._MEIPASS` when `sys.frozen` (PyInstaller stores pure-Python modules in
  an archive, so `__file__` isn't a real path). Source runs are unchanged.
- **Bundled feature set**: every format in the UI dropdown works — core
  (csv/tsv/json/ndjson/sql/xml/cot) plus xlsx, parquet, avro — and NL drafting
  via a pasted Anthropic or OpenAI key (ADR-0013). The spec uses
  `collect_all("pyarrow")` (Arrow DLLs) and `collect_*("faker")` (locale data).
- **Build/release**: `.github/workflows/windows-exe.yml` builds on tags (`v*`),
  manual dispatch, and packaging/app-touching PRs; uploads the artifact and
  attaches `chaff.exe` to the tagged release.

## Consequences — known limitations (be honest with users)
- **Unsigned → SmartScreen.** First launch shows "Windows protected your PC";
  the user clicks *More info → Run anyway*. Authenticode signing (future work)
  removes it.
- **Antivirus false positives.** Onefile PyInstaller binaries are a common
  heuristic FP; `upx=False` reduces this, but an unsigned exe may still be
  quarantined by aggressive AV.
- **Size (~80–100 MB+).** pyarrow's Arrow libraries and faker's locale data
  dominate. The price of "every format works."
- **Slower first launch.** Onefile self-extracts to a temp dir each start; the
  first run is noticeably slower than later ones.
- **Google Gemini NL not bundled.** `google-generativeai` pulls heavy native
  `grpcio`; a pasted Google key returns a clean 502. Anthropic + OpenAI work.
- **Kafka/HTTP/TCP/UDP sinks unreachable** from the UI (unchanged from the web
  app; the exe is the download-only UI, consistent with ADR-0013).
- **Port fallback.** Prefers 8000; if taken (or a second instance), binds a
  free port and opens the browser there, so it never fails to launch.

## Alternatives considered
- **Lean exe** (core + xlsx only): ~40–60 MB, but parquet/avro would appear in
  the dropdown as options that error, or need a per-encoder availability guard.
  Rejected — "every button works" beats a smaller file for this audience.
- **MSI installer** (Briefcase/Inno Setup): Start-menu shortcut, cleaner
  uninstall, but more build machinery and still unsigned. Deferred; the single
  .exe is the simplest thing to hand over.
