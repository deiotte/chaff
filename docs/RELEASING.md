# Desktop releases

[Back to the README](../README.md) · [Operating chaff](OPERATIONS.md)

## What starts a build?

The [Windows workflow](../.github/workflows/windows-exe.yml) and
[macOS workflow](../.github/workflows/macos-app.yml) each listen for a pushed
**tag whose name starts with `v`**. They build that tag's source, smoke-test
launch/shutdown, and create or update the GitHub Release for that tag with
these files:

| Workflow | Release attachments |
|---|---|
| `windows-exe` | `chaff.exe`, `chaff.msi`, `THIRD-PARTY-NOTICES.txt` |
| `macos-app` | `chaff-macos.zip`, containing `chaff.app` |

A merge/push to `main` runs ordinary CI; it does **not** refresh the desktop
release downloads. Publishing or editing a release for an already-pushed tag
also does not start a new build: neither workflow listens to `release: published`.
The trigger is the tag push, not the release page's publication status.

PRs touching application, packaging, manifest, or their own workflow files
build desktop artifacts for review, without attaching them to a release or
signing them. Documentation-only PRs do not trigger desktop builds.

## Cut a new release

After the intended changes are merged and their checks pass, choose an unused
`v*` version tag and push it on the intended commit. For example, **only if
`v0.3.0` is the version you intend to release**:

```bash
git switch main
git pull --ff-only
git tag -a v0.3.0 -m "Chaff v0.3.0"
git push origin v0.3.0
```

Both workflows build independently. A release page can exist with only one
platform's files while the other is building or after it fails; the page's
existence is not proof that both jobs passed. Check both workflows in
[Actions](https://github.com/deiotte/chaff/actions), then confirm all three
app/installer assets are attached to the intended tag in
[Releases](https://github.com/deiotte/chaff/releases).

A `workflow_dispatch` on a branch builds downloadable Actions artifacts, with
no release attachments. To recover a failed tagged release, rerun its failed
workflow/jobs, or dispatch **each** desktop workflow at the existing tag (for
example, `gh workflow run windows-exe.yml --ref v0.3.0` and
`gh workflow run macos-app.yml --ref v0.3.0`). The release jobs run only when
`github.ref` begins `refs/tags/v`. Do not move an existing published tag merely
to rebuild its files.

There is no shared job that gates publication on ordinary `make check` CI or
waits for both platform builds. Verify the intended commit's checks before
pushing the tag and the release assets afterwards.

## What has actually been verified?

As checked on 2026-10-21:

- The last published release is
  [v0.2.0](https://github.com/deiotte/chaff/releases/tag/v0.2.0), published
  2026-09-04, with `chaff.exe`, `chaff.msi`, and `chaff-macos.zip` attached.
- The current plant-feed implementation passed a real
  [Windows build and launch/shutdown check](https://github.com/deiotte/chaff/actions/runs/39585129984)
  and [macOS build and launch/shutdown check](https://github.com/deiotte/chaff/actions/runs/39585130081)
  on its PR. Those PR runs intentionally skipped signing and release attachment.
- The historian change merged afterwards contains a proposed ADR and roadmap
  entry, not a historian implementation.

This supports that building works and tag-based publishing has worked. It is
not a claim that a fresh release from current `main` has already been built,
signed, or exercised by users.

## Signing, architecture, and version labels

Non-PR builds sign Windows packages only if `WINDOWS_PFX_BASE64` and the
associated credentials are configured. macOS signing/notarization similarly
requires its certificate and Apple credentials. Missing certificates allow an
unsigned build with a warning; PRs never receive signing certificates.
Signing configuration and residual governance work are described in
[ADR-0023](adr/0023-installers-and-signing.md) and
[ADR-0031](adr/0031-build-input-integrity.md). Inspect the tag run's signing
steps and package signatures rather than inferring signing from a successful
build. Secret presence was not checked during this documentation review.

The macOS spec builds the runner's native architecture (`target_arch=None`),
not a universal binary. The desktop install includes
`api,formats-extra,nl,nl-openai` plus the core dependencies, which include
the HTTP and MQTT clients (ADR-0047). The Kafka client and Google drafting are
not included; the desktop Stream tab shows Kafka as "not installed". Source and
Docker installs are the full streaming path.

**Version metadata is not derived from the tag today:**

| Location | Current value or rule |
|---|---|
| `pyproject.toml` and `src/chaff/__init__.py` | `0.1.0` |
| macOS `CFBundleShortVersionString` / `CFBundleVersion` | `0.1.0` |
| Windows MSI workflow | `0.1.0.<GITHUB_RUN_NUMBER>` |

Consequently a correctly built `v0.2.0` or later tagged package can still report
`0.1.0`. This is a release/version-alignment gap, separate from whether the
build/upload jobs run. Keep the tag and commit as the release identity until
version propagation is implemented and tested across these locations.

## v0.3.0 Windows attachment failure

The v0.3.0 tag did start both desktop workflows. macOS uploaded successfully.
Windows generated an empty notices file after a `UnicodeEncodeError`: the
runner's default cp1252 encoding could not encode a license author's name.
PowerShell continued into the successful license check, masking the failure.
The release step uploaded the `.exe` and `.msi`, then failed because GitHub
rejects zero-byte assets. Thus the Windows download files can exist even when
that workflow is red; they also carry the empty notices file from that build.

The fix writes notices explicitly as UTF-8, separates generation and license
checking into independent Windows steps, and rejects an empty notices file in
the desktop smoke check. A new release containing the fix is needed to ship
corrected notices inside the Windows packages; rerunning the original v0.3.0
commit alone still uses the old generator. Preserve the published tag.
