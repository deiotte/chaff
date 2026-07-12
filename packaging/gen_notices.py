"""Generate THIRD-PARTY-NOTICES.txt for chaff's bundled dependencies (MIT
attribution + Apache-2.0 NOTICE propagation).

Computes the dependency closure of `chaff[<extras>]` from installed metadata,
then renders each package's license text (and NOTICE, where present) via
pip-licenses. Run in an environment that has the bundled extras installed so
the notices match what ships in the binary.

Usage:
    python packaging/gen_notices.py            # write THIRD-PARTY-NOTICES.txt
    python packaging/gen_notices.py --check    # fail if any non-permissive license
    python packaging/gen_notices.py --extras api,formats-extra,nl,nl-openai

`make notices` / `make license-check` wrap these; the Windows build regenerates
before freezing so the released exe carries current notices.
"""

from __future__ import annotations

import argparse
import importlib.metadata as im
import json
import subprocess
import sys
from pathlib import Path

from packaging.requirements import Requirement

# The extras baked into the distributed builds (must match packaging/chaff.spec
# and the Dockerfile default).
DEFAULT_EXTRAS = "api,formats-extra,nl,nl-openai"

# Licenses we accept in a redistributed build. Anything else (GPL/LGPL/AGPL/…)
# must be resolved before release, so --check fails on it.
ALLOWED = {
    "mit", "bsd", "bsd-2-clause", "bsd-3-clause", "apache", "apache-2.0",
    "apache software license", "psf", "psf-2.0", "python software foundation license",
    "isc", "mpl-2.0", "mozilla public license 2.0 (mpl 2.0)", "the unlicense",
    "unlicense",
}

OUT = Path(__file__).resolve().parent.parent / "THIRD-PARTY-NOTICES.txt"


def _norm(name: str) -> str:
    return name.lower().replace("_", "-")


def closure(extras: set[str]) -> list[str]:
    """Transitive dependency names of chaff[extras], from installed metadata."""
    seen: set[str] = set()
    stack: list[tuple[str, frozenset[str]]] = [("chaff", frozenset(extras))]
    while stack:
        name, exs = stack.pop()
        key = _norm(name)
        if key in seen:
            continue
        seen.add(key)
        try:
            reqs = im.requires(name) or []
        except im.PackageNotFoundError:
            continue
        for raw in reqs:
            try:
                req = Requirement(raw)
            except Exception:
                continue
            marker, ok = req.marker, req.marker is None
            if marker is not None:
                for ex in (exs or frozenset({""})):
                    try:
                        if marker.evaluate({"extra": ex}):
                            ok = True
                            break
                    except Exception:
                        pass
                if not ok:
                    try:
                        ok = marker.evaluate({"extra": ""})
                    except Exception:
                        pass
            if ok:
                stack.append((req.name, frozenset(req.extras)))
    seen.discard("chaff")
    return sorted(seen)


def collect(pkgs: list[str]) -> list[dict]:
    """pip-licenses JSON for the given packages (license + notice text)."""
    out = subprocess.run(
        [sys.executable, "-m", "piplicenses", "--format=json", "--no-license-path",
         "--with-license-file", "--with-notice-file", "--packages", *pkgs],
        capture_output=True, text=True, check=True,
    )
    return json.loads(out.stdout)


def render(entries: list[dict]) -> str:
    lines = [
        "chaff — Third-Party Notices",
        "===========================",
        "",
        "chaff is distributed under the MIT License (see LICENSE). Bundled or",
        "redistributed builds (e.g. the Windows chaff.exe) include the open-source",
        "dependencies below, each reproduced with its license text and, where the",
        "project provides one, its NOTICE file — satisfying the attribution terms",
        "of the MIT, BSD, Apache-2.0, and MPL-2.0 licenses.",
        "",
        f"Covers the bundled dependency set (chaff[{DEFAULT_EXTRAS}]).",
        "Regenerate with: make notices",
        "",
    ]
    for e in sorted(entries, key=lambda x: x["Name"].lower()):
        lines += ["=" * 72, f"{e['Name']} {e['Version']}  —  {e['License']}", "=" * 72, ""]
        text = e.get("LicenseText") or ""
        lines.append(text.strip() if text and text != "UNKNOWN"
                     else "(No license file was packaged with this distribution; "
                          f"license per metadata: {e['License']}.)")
        notice = e.get("NoticeText")
        if notice and notice != "UNKNOWN":
            lines += ["", "-" * 40, "NOTICE:", "-" * 40, notice.strip()]
        lines.append("")
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--extras", default=DEFAULT_EXTRAS)
    ap.add_argument("--check", action="store_true",
                    help="fail if any dependency is under a non-allowlisted license")
    args = ap.parse_args()

    pkgs = closure({e.strip() for e in args.extras.split(",") if e.strip()})
    entries = collect(pkgs)
    found = {_norm(e["Name"]) for e in entries}
    missing = [p for p in pkgs if p not in found]
    if missing:
        print(f"WARNING: no metadata found for: {missing}", file=sys.stderr)

    if args.check:
        bad = [(e["Name"], e["License"]) for e in entries
               if not _license_ok(e["License"])]
        if bad:
            print("NON-PERMISSIVE license(s) found — resolve before release:", file=sys.stderr)
            for name, lic in bad:
                print(f"  {name}: {lic}", file=sys.stderr)
            return 1
        print(f"license-check OK: {len(entries)} bundled deps, all permissive.")
        return 0

    OUT.write_text(render(entries))
    unknown = [e["Name"] for e in entries if (e.get("LicenseText") or "UNKNOWN") == "UNKNOWN"]
    print(f"wrote {OUT.name}: {len(entries)} deps"
          + (f"; no license file for: {unknown}" if unknown else "; all license texts captured"))
    return 0


def _license_ok(lic: str) -> bool:
    lic = (lic or "").strip().lower()
    if lic in ALLOWED:
        return True
    # tolerate compound/expression strings like "MIT OR Apache-2.0", "MIT AND MPL-2.0"
    parts = [p.strip() for chunk in lic.replace("(", " ").replace(")", " ").split(" or ")
             for p in chunk.split(" and ")]
    return bool(parts) and all(any(a in p or p in a for a in ALLOWED) for p in parts if p)


if __name__ == "__main__":
    raise SystemExit(main())
