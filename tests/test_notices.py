"""Regression for v0.3.0's Windows notice-generation failure.

The runner's default cp1252 codec could not encode an author's name, leaving
an empty file that GitHub then refused as a release attachment.
"""

import importlib.util
import sys
from pathlib import Path


def test_notices_preserve_unicode_on_a_cp1252_host(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "chaff_gen_notices", Path("packaging/gen_notices.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    entries = [{"Name": "example", "Version": "1.0", "License": "MIT",
                "LicenseText": "Copyright Łukasz Wiśniewski — MIT permission text",
                "NoticeText": "Attribution: Zoë"}]
    out = tmp_path / "THIRD-PARTY-NOTICES.txt"
    monkeypatch.setattr(module, "OUT", out)
    monkeypatch.setattr(module, "closure", lambda extras: ["example"])
    monkeypatch.setattr(module, "collect", lambda packages: entries)
    monkeypatch.setattr(sys, "argv", ["gen_notices.py"])

    # Keep real filesystem writes, but give an omitted encoding the Windows
    # runner's cp1252 default. This must also fail on Linux before the fix.
    write_text = Path.write_text

    def windows_write_text(path, text, encoding=None, **kwargs):
        return write_text(path, text, encoding=encoding or "cp1252", **kwargs)

    monkeypatch.setattr(Path, "write_text", windows_write_text)
    assert module.main() == 0
    assert out.read_bytes() == module.render(entries).encode("utf-8")


def _gen_notices():
    spec = importlib.util.spec_from_file_location(
        "chaff_gen_notices_lic", Path("packaging/gen_notices.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_license_or_is_a_choice_and_and_binds_every_term():
    """paho-mqtt is "EPL-2.0 OR BSD-3-Clause": we may take it under BSD, so
    it must pass. The old check read OR as AND and rejected it (ADR-0047)."""
    ok = _gen_notices()._license_ok
    assert ok("EPL-2.0 OR BSD-3-Clause")
    assert ok("MIT AND MPL-2.0")
    assert ok("(MIT OR Apache-2.0) AND BSD-3-Clause")
    assert ok("Apache Software License")
    # Trove names carry a bracketed alias; that is not a group (shellingham).
    assert ok("ISC License (ISCL)")
    assert ok("Mozilla Public License 2.0 (MPL 2.0)")
    assert ok("BSD License (BSD-3-Clause) OR GPL-3.0")
    assert not ok("GNU General Public License v3 (GPLv3)")
    assert not ok("MIT AND GPL-3.0")
    assert not ok("(MIT OR GPL-3.0) AND GPL-3.0")
    assert not ok("GPL-3.0")
    assert not ok("LGPL-2.1")
    assert not ok("")
