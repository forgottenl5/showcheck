# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 forgottenl5
"""Demo short (1080x1920, ~21 s, silent): the synthetic 100-drone show, planned placement vs 18 m toward the crowd.

Every ShowCheck number on screen is taken from the CLI's own stdout (saved next to the video as
demo_source_<case>.txt); drone positions come from the same checks run (venue frame, 10 Hz).
Run from the repository root:  python demo/make_demo.py   (needs ffmpeg on PATH)
"""
import contextlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import textwrap

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.collections import LineCollection

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from showcheck import cli                                    # noqa: E402
from showcheck.readers import read_skyc                      # noqa: E402
from showcheck.venue import load_venue                       # noqa: E402
from showcheck.checks import run_checks                      # noqa: E402

W, H, FPS, SPEED = 1080, 1920, 30, 12          # SPEED: playback speed-up of show time (a display choice)
OUT = os.path.join(ROOT, "demo")
BG, FG, DIM = "#0d1117", "#e6edf3", "#8b949e"
COL = {"PASS": "#3fb950", "WARN": "#d29922", "FAIL": "#f85149"}
EXAMPLES = os.path.join(ROOT, "examples")
VENUE = os.path.join(EXAMPLES, "synthetic_venue.json")


def run_case(case):
    """Run the real CLI, keep its stdout, and return (verdict lines, ctx, results)."""
    show = os.path.join(EXAMPLES, f"synthetic_{case}.skyc")
    buf = io.StringIO()
    with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(buf):
        cli.main([show, "--venue", VENUE, "--out", tmp, "--name", case])
    text = buf.getvalue()
    lines = [ln for ln in text.splitlines() if re.match(r"C\d\s+(PASS|WARN|FAIL)\s", ln)]
    with open(os.path.join(OUT, f"demo_source_{case}.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    rows = [re.match(r"(C\d)\s+(PASS|WARN|FAIL)\s+(.*)", ln).groups() for ln in lines]
    results, ctx = run_checks(read_skyc(show), load_venue(VENUE))
    assert [(r.id, r.status, r.summary) for r in results] == [tuple(r) for r in rows], "CLI and checks disagree"
    return rows, ctx, {r.id: r for r in results}


def main():
    venue = json.load(open(VENUE))
    cases = {c: run_case(c) for c in ("clean", "shift18")}
    t = cases["clean"][1]["t"]
    n_anim = int(round((t[-1] - t[0]) / SPEED * FPS))
    # timeline (frames)
    T_TITLE = int(1.5 * FPS)
    T_VERD = int(0.3 * FPS)                      # one verdict line every 0.3 s
    parts = []
    start = T_TITLE
    for case, hold in (("clean", 1.0), ("shift18", 1.4)):
        parts.append((case, start, start + n_anim, start + n_anim + 6 * T_VERD + int(hold * FPS)))
        start = parts[-1][3]
    end_card = (start, start + int(3.0 * FPS))
    total = end_card[1]

    fig = plt.figure(figsize=(W / 100, H / 100), dpi=100, facecolor=BG)
    ax = fig.add_axes([0.06, 0.40, 0.88, 0.47], facecolor=BG)
    ax.set_xlim(-95, 95); ax.set_ylim(-170, 90); ax.set_aspect("equal"); ax.axis("off")
    fence = np.array(venue["geofence"]["polygon"] + venue["geofence"]["polygon"][:1])
    ax.plot(fence[:, 0], fence[:, 1], color=DIM, lw=2)
    ax.text(fence[:, 0].max(), fence[:, 1].max() + 3, "venue fence", color=DIM, fontsize=15, ha="right")
    for c in venue["crowd_areas"]:
        p = np.array(c["polygon"])
        ax.fill(p[:, 0], p[:, 1], color="#f85149", alpha=0.25, lw=0)
        ax.text(*p.mean(0), "CROWD", color="#ff9a94", fontsize=22, ha="center", va="center", weight="bold")
    pads = np.array(venue["pads"])
    ax.plot(pads[:, 0], pads[:, 1], "s", ms=3.5, mfc="none", mec="#3fb950", mew=1)
    trails = LineCollection([], colors="#58a6ff", linewidths=1.0, alpha=0.45); ax.add_collection(trails)
    dots, = ax.plot([], [], "o", ms=4.5, color="#ffd866")
    gap_line, = ax.plot([], [], "-", color="#f85149", lw=3)
    gap_dot, = ax.plot([], [], "o", ms=16, mfc="none", mec="#f85149", mew=3)
    gap_text = ax.text(0, 0, "", color="#f85149", fontsize=22, weight="bold", ha="left", va="center")

    title = fig.text(0.5, 0.955, "", color=FG, fontsize=40, weight="bold", ha="center", va="center")
    sub = fig.text(0.5, 0.915, "", color=DIM, fontsize=22, ha="center", va="center")
    clock = fig.text(0.94, 0.88, "", color=DIM, fontsize=18, ha="right", va="center", family="monospace")
    foot = fig.text(0.5, 0.012, "Synthetic test show and venue (not a real site)", color=DIM, fontsize=15, ha="center")
    vtexts = []
    y = 0.375
    for i in range(6):
        head = fig.text(0.06, y, "", fontsize=24, weight="bold", family="monospace", va="top")
        body = fig.text(0.23, y, "", fontsize=19, color=FG, va="top", linespacing=1.25)
        vtexts.append((head, body)); y -= 0.058
    big = fig.text(0.5, 0.5, "", color=FG, fontsize=40, weight="bold", ha="center", va="center", linespacing=1.4)
    small = fig.text(0.5, 0.40, "", color=DIM, fontsize=22, ha="center", va="center", linespacing=1.5)

    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgba", "-s", f"{W}x{H}", "-r", str(FPS),
           "-i", "-", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "20", "-movflags", "+faststart",
           os.path.join(OUT, "showcheck_demo.mp4")]
    ff = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    for f in range(total):
        for h, b in vtexts:
            h.set_text(""); b.set_text("")
        big.set_text(""); small.set_text("")
        ax.set_visible(True)
        if f < T_TITLE:
            ax.set_visible(f >= T_TITLE // 2)
            title.set_text("ShowCheck"); sub.set_text("Is the show file placed where the venue plan says?")
            clock.set_text(""); dots.set_data([], []); trails.set_segments([]); gap_line.set_data([], []); gap_text.set_text("")
        elif f < end_card[0]:
            case, a0, a1, a2 = next(p for p in parts if p[1] <= f < p[3])
            rows, ctx, res = cases[case]
            P = ctx["P"]
            title.set_text("1 · Planned placement" if case == "clean" else "2 · Moved toward the crowd")
            sub.set_text(f"100-drone synthetic show · top view · {SPEED}× speed")
            k = min(len(t) - 1, int((f - a0) / FPS * SPEED * 10))      # 10 Hz samples
            clock.set_text(f"show time {t[k] - t[0]:5.1f} s")
            dots.set_data(P[:, k, 0], P[:, k, 1])
            k0 = max(0, k - 20)
            trails.set_segments([P[d, k0:k + 1, :2] for d in range(P.shape[0])] if k > 0 else [])
            shown = 0 if f < a1 else min(6, (f - a1) // T_VERD + 1)
            for i, (cid, st, summ) in enumerate(rows[:shown]):
                vtexts[i][0].set_text(f"{cid} {st}"); vtexts[i][0].set_color(COL[st])
                vtexts[i][1].set_text("\n".join(textwrap.wrap(summ, 56)))
            gap_line.set_data([], []); gap_dot.set_data([], []); gap_text.set_text("")
            if case == "shift18" and shown >= 2 and res["C2"].points:
                e, n, _ = res["C2"].points[0]
                ytop = max(p[1] for p in venue["crowd_areas"][0]["polygon"])
                gap_line.set_data([e, e], [n, ytop]); gap_dot.set_data([e], [n])
                gap_text.set_position((e + 6, n + 2))
                gap_text.set_text("closest " + re.match(r"[^:]+: ([\d.]+ m)", rows[1][2]).group(1))   # from the C2 CLI line
        else:
            ax.set_visible(False)
            title.set_text(""); sub.set_text(""); clock.set_text("")
            for h, b in vtexts:
                h.set_text(""); b.set_text("")
            big.set_text("ShowCheck - open source\npre-launch check")
            small.set_text("Synthetic test show. Not a safety certification.\n\ngithub.com/forgottenl5/showcheck")
        fig.canvas.draw()
        ff.stdin.write(bytes(fig.canvas.buffer_rgba()))
    ff.stdin.close()
    if ff.wait() != 0:
        raise SystemExit("ffmpeg failed")
    print(f"{total} frames, {total / FPS:.2f} s -> {cmd[-1]}")


if __name__ == "__main__":
    main()
