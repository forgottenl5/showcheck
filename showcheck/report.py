# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 forgottenl5
"""One HTML page + one top-view PNG per run."""
import html
import json
import os
import datetime
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

COL = {"PASS": "#1b7f3b", "WARN": "#b26a00", "FAIL": "#b3261e"}


def plot_top(show, venue, results, ctx, png):
    fig, ax = plt.subplots(figsize=(9, 9), dpi=110)
    if venue["fence"] is not None:
        f = np.vstack([venue["fence"], venue["fence"][:1]])
        ax.plot(f[:, 0], f[:, 1], "-", color="#444", lw=1.5, label="venue fence")
    if ctx["fence"] is not None:
        f = np.vstack([ctx["fence"], ctx["fence"][:1]])
        ax.plot(f[:, 0], f[:, 1], "--", color="#7a5cff", lw=1.2, label="file fence")
    for i, c in enumerate(venue["crowd"]):
        p = np.vstack([c["poly"], c["poly"][:1]])
        ax.fill(p[:, 0], p[:, 1], color="#f2b8b5", alpha=0.6, label="crowd area" if i == 0 else None)
        ax.text(*c["poly"].mean(0), c["name"], ha="center", va="center", fontsize=8)
    P = ctx["P"]
    stride = max(1, P.shape[1] // 400)
    for d in range(P.shape[0]):
        ax.plot(P[d, ::stride, 0], P[d, ::stride, 1], "-", color="#2a6fdb", lw=0.4, alpha=0.5)
    if len(venue["pads"]):
        ax.plot(venue["pads"][:, 0], venue["pads"][:, 1], "s", ms=3, mfc="none", color="#1b7f3b", label="venue pads")
    ax.plot(ctx["homes"][:, 0], ctx["homes"][:, 1], ".", ms=3, color="#2a6fdb", label="file homes")
    first = True
    for r in results:
        for j, (e, n, lab) in enumerate(r.points):
            ax.plot(e, n, "o", ms=7, color="red", mfc="none", mew=1.8, label="problem" if first else None)
            if j == 0:                                          # one label per check keeps the plot readable
                more = f" (+{len(r.points) - 1} more)" if len(r.points) > 1 else ""
                ax.annotate(f"{r.id}: {lab}{more}", (e, n), fontsize=8, color="red", xytext=(6, 6), textcoords="offset points",
                            bbox=dict(boxstyle="round,pad=0.2", fc="white", ec="none", alpha=0.8))
            first = False
    ax.plot(0, 0, "k+", ms=12, label="venue origin")
    ax.set_aspect("equal"); ax.grid(alpha=0.3)
    ax.set_xlabel("East (m)"); ax.set_ylabel("North (m)")
    ax.set_title(f"Top view (venue frame): {os.path.basename(show.source)}")
    ax.legend(loc="upper right", fontsize=8)
    fig.tight_layout(); fig.savefig(png); plt.close(fig)


def write_report(show, venue, results, ctx, out_dir, name):
    os.makedirs(out_dir, exist_ok=True)
    png = os.path.join(out_dir, f"{name}.png")
    page = os.path.join(out_dir, f"{name}.html")
    plot_top(show, venue, results, ctx, png)
    overall = "FAIL" if any(r.status == "FAIL" for r in results) else "WARN" if any(r.status == "WARN" for r in results) else "PASS"
    P = ctx["P"]
    stats = (f"{len(show.drones)} drones, {ctx['t'][-1] - ctx['t'][0]:.1f} s, max altitude {P[:, :, 2].max():.1f} m, "
             f"format {show.fmt} ({show.grade}-grade), origin {ctx['origin'][0]:.6f}, {ctx['origin'][1]:.6f}, heading {ctx['heading']:.2f} deg")
    rows = "".join(
        f"<tr><td>{r.id}</td><td>{html.escape(r.name)}</td><td class='st' style='background:{COL[r.status]}'>{r.status}</td>"
        f"<td>{html.escape(r.summary)}<ul>{''.join(f'<li>{html.escape(x)}</li>' for x in r.details)}</ul></td></tr>" for r in results)
    notes = "".join(f"<li>{html.escape(n)}</li>" for n in show.notes)
    man_html = ""
    m = next((getattr(r, "manifest", None) for r in results if getattr(r, "manifest", None)), None)
    if m:
        mpath = os.path.join(out_dir, f"{name}.manifest.json")
        with open(mpath, "w", encoding="utf-8") as f:
            json.dump(m, f, indent=1)
        rows_m = "".join(f"<tr><td>{html.escape(e['id'])}</td><td><code>{e['trajectory_sha256']}</code></td>"
                         f"<td><code>{e['lights_sha256']}</code></td></tr>" for e in m["drones"][:1000])
        man_html = (f"<h2>C6 fingerprint manifest</h2><p>Show hash <code>{m['show_sha256']}</code><br>"
                    f"Placement hash (origin, heading, axes, fence) <code>{m['placement_sha256']}</code><br>File hash "
                    f"<code>{m['file_sha256']}</code><br>Method: {html.escape(m['hash_method'])}<br>"
                    f"Machine-readable: <a href=\"{os.path.basename(mpath)}\">{os.path.basename(mpath)}</a> "
                    f"(compare with <code>python -m showcheck.cli compare A.json B.json</code>)</p>"
                    f"<table><tr><th>Drone</th><th>Trajectory SHA-256</th><th>Lights SHA-256</th></tr>{rows_m}</table>")
    doc = f"""<!doctype html><html><head><meta charset="utf-8"><title>ShowCheck {html.escape(name)}</title>
<style>body{{font:14px/1.45 system-ui,sans-serif;margin:24px;max-width:1100px;color:#222;background:#fff}}
table{{border-collapse:collapse;width:100%}}td,th{{border:1px solid #ccc;padding:6px 8px;vertical-align:top;text-align:left}}
.st{{color:#fff;font-weight:700;text-align:center}}.big{{font-size:22px;font-weight:700;color:{COL[overall]}}}
img{{max-width:100%;border:1px solid #ddd}}ul{{margin:4px 0 0 18px;padding:0}}</style></head><body>
<h1>ShowCheck report</h1>
<p class="big">Overall: {overall}</p>
<p><b>Show file:</b> {html.escape(show.source)}<br><b>Venue:</b> {html.escape(venue['name'])}<br>{html.escape(stats)}<br>
<b>Generated:</b> {datetime.datetime.now().isoformat(timespec='seconds')}</p>
<table><tr><th>Check</th><th>Name</th><th>Verdict</th><th>Numbers and where/when</th></tr>{rows}</table>
<h2>Reader notes</h2><ul>{notes}</ul>
<p>All checks use the show's own geometry, moved into the venue frame with the file's origin and heading
(venue values only where the file has none). Positions are sampled at 10 Hz.</p>
<h2>Top view</h2><img src="{os.path.basename(png)}" alt="top view">{man_html}</body></html>"""
    with open(page, "w", encoding="utf-8") as f:
        f.write(doc)
    return page, png, overall
