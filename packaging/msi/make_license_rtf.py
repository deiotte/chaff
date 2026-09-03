"""Render LICENSE as the minimal RTF the MSI license dialog needs.

WiX's license page only accepts RTF. Shipping a hand-made copy would mean two
licence texts that drift; this generates it from the real LICENSE at build
time instead.

Usage: python packaging/msi/make_license_rtf.py <out.rtf>
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def to_rtf(text: str) -> str:
    # RTF is latin-1 with backslash escapes; anything outside it becomes a
    # \uN escape. MIT is ASCII, but the copyright line is user-editable.
    out = []
    for ch in text:
        if ch in "\\{}":
            out.append("\\" + ch)
        elif ch == "\n":
            out.append("\\par\n")
        elif ord(ch) < 128:
            out.append(ch)
        else:
            out.append(f"\\u{ord(ch)}?")
    body = "".join(out)
    return ("{\\rtf1\\ansi\\deff0{\\fonttbl{\\f0\\fnil\\fcharset0 Segoe UI;}}"
            "\\fs18\n" + body + "\n}")


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    dest = Path(sys.argv[1])
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(to_rtf((ROOT / "LICENSE").read_text(encoding="utf-8")),
                    encoding="ascii")
    print(f"wrote {dest} ({dest.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
