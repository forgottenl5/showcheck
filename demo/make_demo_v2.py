# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 forgottenl5
"""Demo short v2 (1080x1920, 20 s), written for viewers with the sound off and no drone-show background.

Story: the synthetic 100-drone show is planned 60 m from the crowd; one wrong number moves it 18 m closer;
it is then 42 m from people with a 50 m limit; ShowCheck flags two failures and one warning.

Every number on screen is parsed from ShowCheck's own CLI output (the same lines as demo_source_clean.txt and
demo_source_shift18.txt; the script stops if they differ). Drone positions come from the same check runs (venue frame).
The audio (a soft music bed and one short error sound) is synthesized here with numpy: no samples, no external sources.

Run from the repository root:  python demo/make_demo_v2.py   (needs ffmpeg on PATH)
Writes demo/showcheck_demo.mp4 (with sound), demo/showcheck_demo_silent.mp4 and stills in demo/v2_checks/.
"""
import contextlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import wave

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.collections import LineCollection
from matplotlib.patches import FancyBboxPatch, Rectangle

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from showcheck import cli                                    # noqa: E402
from showcheck.readers import read_skyc                      # noqa: E402
from showcheck.venue import load_venue                       # noqa: E402
from showcheck.checks import run_checks                      # noqa: E402

W, H, FPS, DUR = 1080, 1920, 30, 20.0
SR = 48000
OUT = os.path.join(ROOT, "demo")
STILLS = os.path.join(OUT, "v2_checks")
EXAMPLES = os.path.join(ROOT, "examples")
VENUE = os.path.join(EXAMPLES, "synthetic_venue.json")
BG, FG, DIM, RED, YEL, GRN = "#0b0f14", "#ffffff", "#9aa4ae", "#ff4d4d", "#ffe14d", "#3fb950"
PT = 72 / 100                       # matplotlib points per pixel at dpi 100
S, CX, Y0 = 7.5, 540, 980           # metres -> pixels: x = CX + E*S, y = Y0 - N*S (North up)
SLIDE = (4.5, 6.5)                  # seconds: the show slides toward the crowd
MEASURE = 8.2                       # seconds: measuring line starts
VERDICT_T = (12.3, 13.4, 14.5)      # seconds: the three problem lines appear
END = 17.0


def px(e, n):
    return CX + np.asarray(e) * S, Y0 - np.asarray(n) * S


def run_case(case):
    """Run the real CLI and check its verdict lines are exactly the saved demo_source_<case>.txt."""
    show = os.path.join(EXAMPLES, f"synthetic_{case}.skyc")
    buf = io.StringIO()
    with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(buf):
        cli.main([show, "--venue", VENUE, "--out", tmp, "--name", case])
    lines = [ln for ln in buf.getvalue().splitlines() if re.match(r"C\d\s+(PASS|WARN|FAIL)\s", ln)]
    rows = [re.match(r"(C\d)\s+(PASS|WARN|FAIL)\s+(.*)", ln).groups() for ln in lines]
    saved = open(os.path.join(OUT, f"demo_source_{case}.txt"), encoding="utf-8").read().splitlines()
    assert [re.sub(r"\s+", " ", s) for s in saved] == [re.sub(r"\s+", " ", s) for s in lines], f"{case}: CLI output changed"
    results, ctx = run_checks(read_skyc(show), load_venue(VENUE))
    assert [(r.id, r.status, r.summary) for r in results] == [tuple(r) for r in rows], "CLI and checks disagree"
    return {r[0]: r for r in rows}, ctx, {r.id: r for r in results}


def whole(x):
    """Show a CLI number without decimals only if it is a whole number to the precision printed."""
    v = float(x)
    assert abs(v - round(v)) < 1e-9, f"{x} is not a whole number"
    return f"{round(v)}"


def numbers(clean, shift):
    n = {}
    m = re.fullmatch(r"(.+): ([\d.]+) m \(buffer ([\d.]+)\)", clean["C2"][2]); assert clean["C2"][1] == "PASS"
    n["planned_m"], n["limit_m"] = whole(m.group(2)), whole(m.group(3))
    m = re.match(r"origin ([\d.]+) m", shift["C1"][2]); assert shift["C1"][1] == "FAIL"
    n["moved_m"] = whole(m.group(1))
    m = re.fullmatch(r"(.+): ([\d.]+) m \(buffer ([\d.]+)\)", shift["C2"][2]); assert shift["C2"][1] == "FAIL"
    n["close_m"] = whole(m.group(2)); assert whole(m.group(3)) == n["limit_m"]
    # the file fence is informational only (Skybrush schema), so a fence mismatch is a WARN, not a FAIL
    m = re.match(r"fence match ([\d.]+) m", shift["C3"][2]); assert shift["C3"][1] == "WARN" and float(m.group(1)) > 0
    n["status"] = [shift[c][1] for c in ("C1", "C2", "C3")]
    return n


def smooth(u):
    u = np.clip(u, 0, 1)
    return u * u * (3 - 2 * u)


# ----------------------------------------------------------------------------------------------- audio
def synth_audio(t_error, path):
    """Soft pad + light plucks (C - Am - F - G, 5 s each) and a two-note soft error sound at t_error."""
    n = int(DUR * SR)
    t = np.arange(n) / SR
    chords = [(130.81, 164.81, 196.00, 261.63), (110.00, 130.81, 164.81, 220.00),
              (87.31, 130.81, 174.61, 220.00), (98.00, 146.83, 196.00, 246.94)]
    bed = np.zeros(n)
    seg = DUR / len(chords)
    for i, ch in enumerate(chords):
        c0 = i * seg
        env = np.clip((t - c0 + 0.6) / 1.2, 0, 1) * np.clip((c0 + seg + 0.6 - t) / 1.2, 0, 1)   # 1.2 s crossfades
        env = np.sin(env * np.pi / 2) ** 2
        for f in ch:
            for det in (-0.7, 0.7):
                bed += env * (np.sin(2 * np.pi * (f + det) * t) + 0.15 * np.sin(4 * np.pi * (f + det) * t)) / 8
        for j in range(int(seg / 0.5)):                          # quiet plucks, one octave up, every 0.5 s
            ts = c0 + j * 0.5
            f = ch[(j * 2) % len(ch)] * 2
            k = (t >= ts)
            bed += k * 0.10 * np.sin(2 * np.pi * f * (t - ts)) * np.exp(-np.clip(t - ts, 0, None) * 6)
    bed *= np.clip(t / 1.0, 0, 1) * np.clip((DUR - t) / 2.0, 0, 1)
    duck = 1 - 0.45 * np.exp(-((t - t_error - 0.25) / 0.45) ** 2)  # bed dips under the error sound
    bed *= duck
    err = np.zeros(n)
    for ts, f, d in ((t_error, 493.88, 0.16), (t_error + 0.16, 349.23, 0.40)):   # B4 then F4, soft "uh-oh"
        k = (t >= ts) & (t < ts + d + 0.3)
        tt = t[k] - ts
        env = np.clip(tt / 0.008, 0, 1) * np.exp(-tt / (d * 0.55))
        err[k] += env * (np.sin(2 * np.pi * f * tt) + 0.25 * np.sin(6 * np.pi * f * tt) / 3)
    mix = bed / np.abs(bed).max() * 0.35 + err * 0.55
    pcm = (np.clip(mix, -1, 1) * 32767).astype("<i2")
    with wave.open(path, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR); w.writeframes(pcm.tobytes())


def loudnorm(src, dst):
    """Two-pass EBU R128 normalisation to -16 LUFS, true peak -2.0 dBTP (0.5 dB margin for the AAC encode)."""
    target = "I=-16:TP=-2.0:LRA=11"
    r = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", src, "-af", f"loudnorm={target}:print_format=json",
                        "-f", "null", "-"], capture_output=True, text=True)
    m = json.loads(r.stderr[r.stderr.rindex("{"):r.stderr.rindex("}") + 1])
    af = (f"loudnorm={target}:measured_I={m['input_i']}:measured_TP={m['input_tp']}:measured_LRA={m['input_lra']}"
          f":measured_thresh={m['input_thresh']}:offset={m['target_offset']}:linear=true,aresample={SR}")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", src, "-af", af, "-ac", "2", dst], check=True)


# ----------------------------------------------------------------------------------------------- video
def main():
    os.makedirs(STILLS, exist_ok=True)
    clean, ctx_c, res_c = run_case("clean")
    shift, ctx_s, res_s = run_case("shift18")
    num = numbers(clean, shift)
    venue = json.load(open(VENUE))
    crowd = np.array(venue["crowd_areas"][0]["polygon"])
    edge_n = crowd[:, 1].max()                                    # crowd edge facing the show
    assert whole(venue["crowd_areas"][0]["buffer_m"]) == num["limit_m"]
    limit_n = edge_n + float(num["limit_m"])

    # the moment the show is closest to the crowd (C2's own point in the shifted run)
    e_c, n_c, _ = res_s["C2"].points[0]
    Ps, Pc = ctx_s["P"], ctx_c["P"]
    d, k = np.unravel_index(np.argmin(np.hypot(Ps[:, :, 0] - e_c, Ps[:, :, 1] - n_c)), Ps.shape[:2])
    shift_vec = (Ps[:, :, :2] - Pc[:, :, :2]).reshape(-1, 2)
    assert np.allclose(shift_vec, shift_vec[0], atol=1e-6)            # the whole show moved by one vector
    assert abs(np.hypot(*shift_vec[0]) - float(num["moved_m"])) < 0.01
    assert abs((Pc[d, k, 1] - edge_n) - float(num["planned_m"])) < 0.05 and abs((n_c - edge_n) - float(num["close_m"])) < 0.05
    dots0, dots1 = Pc[:, k, :2], Ps[:, k, :2]
    trail0 = [Pc[i, ::3, :2] for i in range(len(Pc))]
    vec = shift_vec[0]

    def u_at(ts):
        return smooth((ts - SLIDE[0]) / (SLIDE[1] - SLIDE[0]))
    # when the closest drone crosses the safety line
    tt = np.arange(SLIDE[0], SLIDE[1], 1 / SR)
    t_cross = tt[np.argmax(Pc[d, k, 1] + u_at(tt) * vec[1] < limit_n)]

    fig = plt.figure(figsize=(W / 100, H / 100), dpi=100, facecolor=BG)
    ax = fig.add_axes([0, 0, 1, 1]); ax.set_xlim(0, W); ax.set_ylim(H, 0); ax.axis("off"); ax.set_facecolor(BG)
    cx0, cy0 = px(crowd[:, 0].min(), edge_n)
    ax.add_patch(Rectangle((cx0, cy0), np.ptp(crowd[:, 0]) * S, H - cy0 + 10, fc="#5a1f22", ec=RED, lw=3))
    ax.text(CX, (cy0 + H) / 2, "CROWD", color="#ffb3b3", fontsize=62 * PT, weight="bold", ha="center", va="center")
    ly = px(0, limit_n)[1]
    limit_line, = ax.plot([20, W - 20], [ly, ly], "--", color=FG, lw=4, dashes=(6, 4))
    limit_lbl = ax.text(24, ly + 14, f"safety limit\n{num['limit_m']} m", color=FG, fontsize=42 * PT,
                        ha="left", va="top", weight="bold", linespacing=1.1)
    trails = LineCollection([], colors="#4da3ff", linewidths=1.2, alpha=0.22); ax.add_collection(trails)
    dots, = ax.plot([], [], "o", ms=17 * PT * 1.0, color=YEL, mec="#fff7c2", mew=1)
    meas, = ax.plot([], [], "-", color=RED, lw=5, solid_capstyle="butt")
    meas_ends, = ax.plot([], [], "_", color=RED, ms=40 * PT, mew=5)
    meas_lbl = ax.text(0, 0, "", color=RED, fontsize=64 * PT, weight="bold", ha="left", va="center",
                       bbox=dict(boxstyle="round,pad=0.25", fc=BG, ec="none", alpha=0.85))
    brand = ax.text(CX, 70, "ShowCheck  ·  synthetic test show", color=DIM, fontsize=30 * PT, ha="center", va="center")
    cap = ax.text(CX, 330, "", color=FG, fontsize=58 * PT, weight="bold", ha="center", va="center", linespacing=1.25,
                  bbox=dict(boxstyle="round,pad=0.55", fc="#000000", ec="none", alpha=0.82))
    small = ax.text(CX, 150, "", color=FG, fontsize=44 * PT, ha="center", va="center",
                    bbox=dict(boxstyle="round,pad=0.4", fc="#000000", ec="none", alpha=0.82))
    panel = FancyBboxPatch((40, 215), W - 80, 570, boxstyle="round,pad=0,rounding_size=24", fc="#000000", ec="none",
                           alpha=0.86, visible=False)
    ax.add_patch(panel)
    verdicts = [
        f"Show is {num['moved_m']} m off\nits planned spot",
        f"Too close to the crowd:\n{num['close_m']} m (limit {num['limit_m']} m)",
        "Geofence doesn't match\nthe venue",
    ]
    vrows = []
    y = 250
    for v, stv in zip(verdicts, num["status"]):
        col = RED if stv == "FAIL" else YEL
        x1, = ax.plot([], [], "-", lw=9, solid_capstyle="round")
        x2, = ax.plot([], [], "-", lw=9, solid_capstyle="round")
        tx = ax.text(165, y, v, color=FG, fontsize=56 * PT, weight="bold", va="top",
                     linespacing=1.15)
        x1.set_color(col); x2.set_color(col); x2.set_marker("o"); x2.set_markersize(12)
        vrows.append((y, x1, x2, tx, stv)); y += 180
    end_txt = [
        ax.text(CX, 760, "ShowCheck -\ncheck the show file\nbefore launch", color=FG, fontsize=76 * PT, weight="bold",
                ha="center", va="center", linespacing=1.25),
        ax.text(CX, 1010, "Free and open source", color=GRN, fontsize=56 * PT, weight="bold", ha="center", va="center"),
        ax.text(CX, 1150, "github.com/forgottenl5/showcheck", color=YEL, fontsize=52 * PT, ha="center", va="center"),
        ax.text(CX, 1700, "Synthetic test show. Not a safety certification.", color=DIM, fontsize=36 * PT,
                ha="center", va="center"),
    ]
    scene_art = [limit_line, limit_lbl, trails, dots, meas, meas_ends, meas_lbl, brand] + ax.patches[:1] + ax.texts[:1]
    captions = [(0, 4, f"A drone show is planned\n{num['planned_m']} m from the crowd."),
                (4, 8, f"One wrong number in the\nfile moves it {num['moved_m']} m closer."),
                (8, 12, f"{num['close_m']} m from people.\nThe safety limit is {num['limit_m']} m.")]

    def draw(ts):
        end = ts >= END
        for a in scene_art:
            a.set_visible(not end)
        for a in end_txt:
            a.set_visible(end)
            a.set_alpha(min(1.0, (ts - END) / 0.5) if end else 1)
        u = u_at(ts)
        P = dots0 + u * vec
        dots.set_data(*px(P[:, 0], P[:, 1]))
        trails.set_segments([np.column_stack(px(tr[:, 0] + u * vec[0], tr[:, 1] + u * vec[1])) for tr in trail0])
        crossed = ts >= t_cross
        limit_line.set_color(RED if crossed else FG); limit_lbl.set_color(RED if crossed else FG)
        # measuring line from the closest drone to the crowd edge
        g = np.clip((ts - MEASURE) / 0.6, 0, 1)
        if g > 0 and not end:
            xa, ya = px(e_c, n_c); _, yb = px(e_c, edge_n)
            yb = ya + (yb - ya) * smooth(g)
            meas.set_data([xa, xa], [ya + 12, yb]); meas_ends.set_data([xa, xa], [ya + 12, yb])
            meas_lbl.set_position((xa + 26, (ya + yb) / 2)); meas_lbl.set_text(f"{num['close_m']} m" if g >= 1 else "")
        else:
            meas.set_data([], []); meas_ends.set_data([], []); meas_lbl.set_text("")
        c = next((txt for a, b, txt in captions if a <= ts < b), "")
        cap.set_text(c); cap.set_visible(bool(c))
        in_v = 12 <= ts < END
        small.set_text("ShowCheck flags it before launch." if in_v else ""); small.set_visible(in_v)
        panel.set_visible(in_v)
        for (yy, x1, x2, tx, stv), tv in zip(vrows, VERDICT_T):
            on = in_v and ts >= tv
            tx.set_visible(on)
            xc, yc, r = 100, yy + 34, 26
            if stv == "FAIL":                                     # red cross
                x2.set_marker("")
                x1.set_data(([xc - r, xc + r], [yc - r, yc + r]) if on else ([], []))
                x2.set_data(([xc - r, xc + r], [yc + r, yc - r]) if on else ([], []))
            else:                                                 # yellow exclamation mark (WARN)
                x2.set_linestyle("none")
                x1.set_data(([xc, xc], [yc - r - 4, yc + r - 18]) if on else ([], []))
                x2.set_data(([xc], [yc + r + 2]) if on else ([], []))

    total = int(DUR * FPS)
    silent = os.path.join(OUT, "showcheck_demo_silent.mp4")
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgba", "-s", f"{W}x{H}", "-r", str(FPS),
           "-i", "-", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "20", "-movflags", "+faststart", silent]
    ff = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    for f in range(total):
        draw(f / FPS)
        fig.canvas.draw()
        ff.stdin.write(bytes(fig.canvas.buffer_rgba()))
    ff.stdin.close()
    if ff.wait() != 0:
        raise SystemExit("ffmpeg failed")
    for s in (2, 6, 10, 14, 19):                                  # stills straight from the encoded video
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", str(s), "-i", silent, "-frames:v", "1",
                        os.path.join(STILLS, f"v2_{s:02d}s.png")], check=True)

    with tempfile.TemporaryDirectory() as tmp:
        raw, norm = os.path.join(tmp, "raw.wav"), os.path.join(tmp, "norm.wav")
        synth_audio(t_cross, raw)
        loudnorm(raw, norm)
        final = os.path.join(OUT, "showcheck_demo.mp4")
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", silent, "-i", norm, "-map", "0:v", "-map", "1:a",
                        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart", final],
                       check=True)
    print(json.dumps({"numbers": num, "closest_drone_t": round(float(ctx_c["t"][k]), 2), "t_cross_s": round(float(t_cross), 3),
                      "frames": total}, indent=1))


if __name__ == "__main__":
    main()
