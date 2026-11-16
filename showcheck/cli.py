# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 forgottenl5
"""Command line.

  python -m showcheck.cli SHOW --venue VENUE.json [--out DIR] [--name NAME] [--expect MANIFEST.json]
      run C1-C6, write NAME.html, NAME.png and NAME.manifest.json; exit 1 if any FAIL
  python -m showcheck.cli compare A.manifest.json B.manifest.json
      list drones whose trajectory / lights / pyro hashes differ or that are missing; exit 1 if anything differs
  python -m showcheck.cli anonymize IN.skyc OUT.skyc [--venue IN.json OUT.json]
      move the origin to a fixed fake location (lat 0.0100, lon 0.0100) and strip names/metadata; geometry is kept.
      With --venue the venue moves to the same fake origin and the show keeps its offset from it.
"""
import argparse
import json
import os
import sys
from .readers import read_show
from .venue import load_venue
from .checks import run_checks
from .report import write_report
from .fingerprint import compare


def verdict_table(results):
    lines = [f"{'Check':<6}{'Verdict':<9}Summary", "-" * 78]
    lines += [f"{r.id:<6}{r.status:<9}{r.summary}" for r in results]
    return "\n".join(lines)


def cmd_compare(argv):
    ap = argparse.ArgumentParser(prog="showcheck.cli compare", description="Compare two ShowCheck manifests")
    ap.add_argument("a"); ap.add_argument("b")
    a = ap.parse_args(argv)
    A, B = (json.load(open(p, encoding="utf-8")) for p in (a.a, a.b))
    c = compare(A, B)
    print(f"A: {a.a}  ({A['drone_count']} drones, show {A['show_sha256'][:16]}...)")
    print(f"B: {a.b}  ({B['drone_count']} drones, show {B['show_sha256'][:16]}...)")
    if c["same"]:
        print("IDENTICAL: every drone's trajectory and light hashes match.")
        return 0
    for i, f in c["differ"]:
        print(f"DIFFERS  {i}: {', '.join(f)}")
    for i in c["missing_in_b"]:
        print(f"MISSING in B: {i}")
    for i in c["missing_in_a"]:
        print(f"MISSING in A: {i}")
    if not c["placement_same"]:
        print(f"PLACEMENT differs (origin / heading / axes / fence):\n  A {A.get('placement')}\n  B {B.get('placement')}")
    print(f"\n{len(c['differ'])} differ, {len(c['missing_in_b'])} missing in B, {len(c['missing_in_a'])} missing in A, "
          f"placement {'same' if c['placement_same'] else 'DIFFERENT'}")
    return 1


def cmd_anonymize(argv):
    from .anonymize import anonymize_skyc, anonymize_venue
    ap = argparse.ArgumentParser(prog="showcheck.cli anonymize", description="Anonymize a .skyc for sharing")
    ap.add_argument("src"); ap.add_argument("dst")
    ap.add_argument("--venue", nargs=2, metavar=("IN", "OUT"), help="also write the venue file with the same fake origin")
    a = ap.parse_args(argv)
    if os.path.abspath(a.src) == os.path.abspath(a.dst):
        ap.error("output must be a different file")
    v = vo = None
    if a.venue:
        with open(a.venue[0], encoding="utf-8") as f:
            v0 = json.load(f)
        vo, v = (v0["origin"]["lat"], v0["origin"]["lon"]), anonymize_venue(v0)
    for line in anonymize_skyc(a.src, a.dst, vo):
        print(line)
    if a.venue:
        with open(a.venue[1], "w", encoding="utf-8") as f:
            json.dump(v, f, indent=1)
        print(f"venue written with the same fake origin: {a.venue[1]}")
    print(f"written: {a.dst}  (check it before sharing: python -m showcheck.cli {a.dst} --venue ...)")
    return 0


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == "compare":
        return cmd_compare(argv[1:])
    if argv and argv[0] == "anonymize":
        return cmd_anonymize(argv[1:])
    ap = argparse.ArgumentParser(description="ShowCheck: pre-launch checks for a drone-show file against a venue file")
    ap.add_argument("show")
    ap.add_argument("--venue", required=True)
    ap.add_argument("--out", default="reports")
    ap.add_argument("--name", default=None)
    ap.add_argument("--expect", default=None, help="manifest to compare against (C6 FAILs on any difference)")
    a = ap.parse_args(argv)
    show, venue = read_show(a.show), load_venue(a.venue)
    expected = json.load(open(a.expect, encoding="utf-8")) if a.expect else None
    results, ctx = run_checks(show, venue, expected)
    name = a.name or os.path.splitext(os.path.basename(a.show))[0]
    page, png, overall = write_report(show, venue, results, ctx, a.out, name)
    print(verdict_table(results))
    print(f"\nOverall: {overall}\nReport: {os.path.abspath(page)}\nTop view: {os.path.abspath(png)}\n"
          f"Manifest: {os.path.abspath(os.path.join(a.out, name + '.manifest.json'))}")
    return 1 if overall == "FAIL" else 0


if __name__ == "__main__":
    sys.exit(main())
