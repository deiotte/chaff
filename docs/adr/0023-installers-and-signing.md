# ADR-0023: Installers, a Quit button, and signing we can't do for you

- Status: Accepted
- Owner: Karl (implemented by Claude Code)
- Date: 2026-09-03

## Context

Phase 5 had two open items: Authenticode signing to drop the SmartScreen
"Run anyway" prompt, and a macOS `.app` / Windows MSI with a Start-menu
shortcut. ADR-0014 shipped the bare `chaff.exe` and deferred both, noting the
MSI was "more build machinery and still unsigned."

Two things forced the shape of this change.

**Signing needs a certificate that costs money and can't be automated into
existence.** Windows Authenticode requires an OV or EV code-signing
certificate (~$200–700/yr) which, since the CA/Browser Forum's June 2023
rules, must live on a hardware token or cloud HSM — a plain `.pfx` in CI is no
longer issuable from a public CA. macOS notarization requires a paid Apple
Developer account ($99/yr) and a Developer ID certificate. Neither can be
obtained by writing code. The honest deliverable is the *pipeline*, ready for
credentials that only the project owner can buy.

**A macOS `.app` has no console window.** The whole Windows quit story is
"close the black window." A bundle launched from Finder runs windowless, so
the same app on macOS would start, open a browser, and then have no way to
stop short of Force Quit. Shipping that would be worse than shipping nothing.

## Decision

**1. Desktop mode gets a real Quit path.** The launcher sets `CHAFF_DESKTOP=1`
and registers a shutdown hook; the API exposes `POST /shutdown` and the UI
shows a **Quit chaff** button. This is the macOS bundle's only quit story and
an improvement on Windows, where finding the console window is its own small
indignity.

It is **off by default and loopback-only**. A public `/shutdown` on the Docker
deployment would be a one-click denial of service, so the route 404s unless
desktop mode is armed, and refuses any non-loopback caller even then. The
launcher already binds `127.0.0.1` only; the origin check is defence in depth.

Stopping goes through a hook the launcher registers rather than a signal,
because the launcher owns the uvicorn `Server` object and signal semantics
differ on Windows. One path on every platform.

**2. macOS ships a `.app` bundle.** `packaging/chaff-macos.spec` is onedir
(a `.app` *is* a directory; onefile inside one re-extracts on every launch for
no benefit) with `console=False` and a `BUNDLE` step. Distributed as a zip
made with `ditto`, which preserves bundle structure and any signature — a
plain artifact zip loses the executable bit.

**3. Windows ships an MSI alongside the exe.** WiX v5, wrapping the *same*
`dist/chaff.exe` — the installer is packaging, never a second way to build the
app. It adds a Start-menu shortcut (a bare `.exe` lands in Downloads and is
lost), an Add/Remove Programs entry, and `MajorUpgrade` so upgrades replace
rather than stack. `Scope="perUser"` avoids an admin prompt. The licence page
is generated from `LICENSE` at build time, so there is no second copy to
drift.

**4. Signing is wired, gated, and loud when it doesn't happen.** Both
workflows check for credentials, sign when present, and emit a GitHub
`::warning` when absent saying the artifact is unsigned and what users will
see. Signing material is written to a temp file and removed in a `finally` /
`trap cleanup EXIT`, so a private key never outlives its step even on failure.

**5. CI smoke-tests the real artifact.** The old bar was "the file exists,"
which cannot catch the failure mode frozen builds actually have: a lazily
imported module that didn't make it into the bundle and raises ImportError on
a user's machine. Both workflows now start the built artifact, assert
`/registry` answers with desktop mode armed, and stop it via `/shutdown` —
proving the bundle runs and the Quit path works. `CHAFF_DESKTOP_PORT` pins the
port so the check can't miss a fallback bind, and `CHAFF_NO_BROWSER=1` keeps
it headless. Both knobs are useful to real users too.

**6. One bundle config for both specs.** `packaging/bundle_config.py` holds
the data files, hidden imports and excludes that both specs import. Duplicated
lists are precisely how a format ends up working on Windows and raising
ImportError on macOS months later, with no test to catch it.

## What you must buy for signing to actually happen

Nothing in this ADR signs anything until these exist. Until then both
platforms build and run — unsigned, with the warnings below.

**Windows** — an OV or EV code-signing certificate. Since June 2023 the
private key must be on hardware or in a cloud HSM, so the plain-`.pfx` path
wired here works with a certificate you already hold or export, not with a
freshly issued one from a public CA. If you're buying new, **Azure Trusted
Signing** (~$10/month, identity validation required) is the CI-friendly
option and would replace the `signtool /f` step with its GitHub Action.

| Secret | What it is |
| --- | --- |
| `WINDOWS_PFX_BASE64` | base64 of the `.pfx` |
| `WINDOWS_PFX_PASSWORD` | its password |

**macOS** — Apple Developer Program membership ($99/yr), a **Developer ID
Application** certificate (not "Mac App Distribution"), and an app-specific
password for notarization.

| Secret | What it is |
| --- | --- |
| `MACOS_CERT_BASE64` | base64 of the Developer ID `.p12` |
| `MACOS_CERT_PASSWORD` | its password |
| `MACOS_SIGN_IDENTITY` | e.g. `Developer ID Application: Your Name (TEAMID)` |
| `MACOS_NOTARY_APPLE_ID` | the Apple ID |
| `MACOS_NOTARY_TEAM_ID` | the 10-character team ID |
| `MACOS_NOTARY_PASSWORD` | an app-specific password |

## Consequences — be honest with users

- **Unsigned Windows builds still show SmartScreen.** Unchanged from
  ADR-0014: *More info → Run anyway*. The MSI shows it too.
- **Unsigned macOS builds are worse than Windows.** Gatekeeper on a
  downloaded, unsigned, un-notarized bundle requires right-click → **Open** →
  Open (or System Settings → Privacy & Security → "Open Anyway"). A plain
  double-click just refuses, and it does not say why. This is documented in
  the README, but it is a genuinely poor first impression and the strongest
  practical argument for buying the Apple membership.
- **The macOS build is single-architecture** — whatever `macos-latest` is
  (arm64 today). Intel Macs are not covered. A universal2 build needs
  universal wheels for every native dependency, which pyarrow does not
  reliably ship.
- **`codesign --deep` is deprecated by Apple** but remains the working option
  for PyInstaller bundles, which contain many nested unsigned `.dylib`/`.so`
  files. If it stops working, the replacement is signing inner binaries
  individually, innermost first.
- **The signing paths are untested.** Nobody has run them with real
  credentials — there are none. They are written from the documented
  interfaces and will likely need a round of fixes on first use with a real
  certificate. The gating means that until then, they cannot break the build.
- **CI cost grows**: a macOS runner per tagged release and packaging PR.

## Alternatives considered

- **Ship a `.dmg` instead of a zip.** Prettier (the drag-to-Applications
  window), and worth doing later. It adds `create-dmg` and a background image
  to maintain, and does nothing about the Gatekeeper problem, which is the
  actual barrier.
- **A `.pkg` installer for macOS.** Installs to `/Applications` properly, but
  needs its own *Developer ID Installer* certificate — a second thing to buy
  before it improves on the zip.
- **Inno Setup instead of WiX.** Simpler markup, but produces an `.exe`
  installer rather than an MSI, and no Add/Remove Programs integration without
  extra work.
- **Skip the Quit button; tell macOS users to Force Quit.** Rejected. "Open
  Activity Monitor" is not something to write in a README aimed at someone who
  was promised they wouldn't need an engineer.
