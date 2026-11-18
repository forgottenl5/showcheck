# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 forgottenl5
"""Checks C1-C5 (C6 is in fingerprint.py). Each returns a Result with PASS / FAIL / WARN, the numbers, and where/when."""
from dataclasses import dataclass, field
import numpy as np
from .model import sample, local_to_venue, heading_diff, geo_offset, RATE_HZ

PASS, WARN, FAIL = "PASS", "WARN", "FAIL"


@dataclass
class Result:
    id: str
    name: str
    status: str
    summary: str
    details: list = field(default_factory=list)
    points: list = field(default_factory=list)     # (east, north, label) problem points for the plot


def worst(*s):
    return FAIL if FAIL in s else WARN if WARN in s else PASS


# ------------------------------------------------------------ geometry
def inside(pts, poly):
    x, y = pts[:, 0], pts[:, 1]
    res = np.zeros(len(pts), bool)
    for (x1, y1), (x2, y2) in zip(poly, np.roll(poly, -1, 0)):
        cross = (y1 > y) != (y2 > y)
        with np.errstate(divide="ignore", invalid="ignore"):
            xi = (x2 - x1) * (y - y1) / (y2 - y1) + x1
        res ^= cross & (x < xi)
    return res


def edge_dist(pts, poly):
    best = np.full(len(pts), np.inf)
    for a, b in zip(poly, np.roll(poly, -1, 0)):
        ab = b - a
        u = np.clip(((pts - a) @ ab) / max(ab @ ab, 1e-12), 0, 1)
        best = np.minimum(best, np.linalg.norm(pts - (a + u[:, None] * ab), axis=1))
    return best


# ------------------------------------------------------------ frame
def prepare(show, venue):
    """Sample the show at 10 Hz and put it into venue ENU using the show's own origin + heading."""
    origin = show.origin or venue["origin"]
    heading = show.heading if show.heading is not None else venue["heading"]
    t, P = sample(show)
    Pv = local_to_venue(P, origin, heading, show.handed, venue["origin"])
    homes = local_to_venue(np.array([d.home for d in show.drones]), origin, heading, show.handed, venue["origin"])
    fence = None if show.fence is None else local_to_venue(show.fence, origin, heading, show.handed, venue["origin"])
    dz = float(geo_offset(origin, venue["origin"])[2])        # venue-frame z = show Z + dz
    return {"t": t, "P": Pv, "homes": homes, "fence": fence, "origin": origin, "heading": heading, "dz": dz}


def pick_limit(show, venue, venue_key, file_key, file_name):
    """(value, where it came from) for one limit: the venue value wins if set, else the file's settings.validation."""
    v = venue["limits"].get(venue_key)
    f = (getattr(show, "limits", None) or {}).get(file_key)
    if v is not None:
        return float(v), f"venue limits.{venue_key}" + (f" (file {file_name} {f:g} not used: venue wins)" if f is not None else "")
    if f is not None:
        return float(f), f"file settings.validation.{file_name}"
    return None, None


def _where(ctx, show, d, k):
    e, n, u = ctx["P"][d, k]
    return f"drone {show.drones[d].id} at t={ctx['t'][k]:.1f} s (E {e:.1f}, N {n:.1f}, alt {u:.1f} m)"


# ------------------------------------------------------------ C1
def c1_origin(show, venue, ctx):
    tol, det, pts, st = venue["tol"], [], [], []
    if show.origin is None:
        st.append(WARN); det.append("file has no origin: venue origin assumed, NOT verified")
        od = 0.0
    else:
        off = geo_offset(show.origin, venue["origin"])
        od = float(np.hypot(off[0], off[1]))
        st.append(FAIL if od > tol["origin_m"] else PASS)
        det.append(f"origin offset {od:.2f} m (E {off[0]:+.2f}, N {off[1]:+.2f}), altitude {off[2]:+.2f} m; limit {tol['origin_m']} m")
    if show.heading is None:
        st.append(WARN); det.append(f"file has no heading: venue heading {venue['heading']:.1f} deg assumed, NOT verified")
        hd = 0.0
    else:
        hd = heading_diff(show.heading, venue["heading"])
        st.append(FAIL if abs(hd) > tol["heading_deg"] else PASS)
        det.append(f"heading file {show.heading:.2f} vs venue {venue['heading']:.2f} deg: difference {hd:+.2f} deg; limit {tol['heading_deg']} deg")
    pads, assign = venue["pads"], venue["pad_assignment"]
    if len(pads) == 0:
        st.append(WARN); det.append("venue lists no pads: home positions not checked")
        miss = []
    else:
        H = ctx["homes"][:, :2]
        if assign:                                           # each drone against ITS pad
            idx = np.array([assign.get(d.id, -1) for d in show.drones])
            dist = np.where(idx >= 0, np.linalg.norm(H - pads[np.maximum(idx, 0)], axis=1), np.inf)
            nopad = [show.drones[i].id for i in np.where(idx < 0)[0]]
            dup = len(set(idx[idx >= 0])) < (idx >= 0).sum()
            how = "assigned pad"
            if nopad:
                det.append(f"{len(nopad)} drone(s) have no pad assignment: {', '.join(nopad[:10])}")
            if dup:
                det.append("pad_assignment gives the same pad to more than one drone")
                st.append(FAIL)
        else:                                                # optimal one-to-one matching, not nearest pad
            from scipy.optimize import linear_sum_assignment
            C = np.linalg.norm(H[:, None] - pads[None], axis=2)
            r, c = linear_sum_assignment(C)
            dist = np.full(len(H), np.inf); dist[r] = C[r, c]
            how = "one-to-one matched pad"
            st.append(WARN)
            det.append("venue has no pad_assignment: drones matched to pads one-to-one (minimum total distance); "
                       "a swapped drone/pad pair cannot be detected this way")
            if len(H) > len(pads):
                det.append(f"{len(H) - len(pads)} more drones than pads")
        miss = np.where(dist > tol["pad_m"])[0]
        st.append(FAIL if len(miss) else PASS)
        fin = dist[np.isfinite(dist)]
        det.append(f"homes within {tol['pad_m']} m of their {how}: {len(dist) - len(miss)}/{len(dist)}"
                   + (f"; worst {fin.max():.2f} m" if len(fin) else ""))
        if len(miss):
            det.append("drones off-pad: " + ", ".join(f"{show.drones[i].id} ({dist[i]:.2f} m)" for i in miss[:15])
                       + (f" ... +{len(miss) - 15} more" if len(miss) > 15 else ""))
            pts += [(*ctx["homes"][i, :2], "off-pad home") for i in miss]
    return Result("C1", "Origin, heading, pads", worst(*st),
                  f"origin {od:.2f} m, heading {hd:+.2f} deg, {len(miss)} drone(s) off-pad", det, pts)


# ------------------------------------------------------------ C2
def c2_crowd(show, venue, ctx):
    if not venue["crowd"]:
        return Result("C2", "Crowd distance", WARN, "venue lists no crowd areas", ["nothing checked"])
    P = ctx["P"]
    flat = P[:, :, :2].reshape(-1, 2)
    det, pts, st, summ = [], [], [], []
    for c in venue["crowd"]:
        d = edge_dist(flat, c["poly"])
        d[inside(flat, c["poly"])] = 0.0
        k = int(np.argmin(d)); di, ki = divmod(k, P.shape[1])
        ok = d[k] >= c["buffer"]
        st.append(PASS if ok else FAIL)
        summ.append(f"{c['name']}: {d[k]:.1f} m (buffer {c['buffer']:g})")
        det.append(f"{c['name']}: closest horizontal distance {d[k]:.2f} m vs buffer {c['buffer']:g} m - {_where(ctx, show, di, ki)}")
        if not ok:
            pts.append((*P[di, ki, :2], f"crowd {d[k]:.1f} m"))
    return Result("C2", "Crowd distance", worst(*st), "; ".join(summ), det, pts)


# ------------------------------------------------------------ C3
def c3_fence(show, venue, ctx):
    det, pts, st = [], [], []
    vf = venue["fence"]
    if vf is None:
        return Result("C3", "Geofence", WARN, "venue has no geofence", ["nothing checked"])
    if ctx["fence"] is None:
        st.append(WARN); det.append("file has no geofence (" + ("visualization-grade file" if show.grade != "flight" else "not set") + "): only the venue fence is used")
        mvd = None
    else:
        ff = ctx["fence"]
        dm = np.linalg.norm(ff[:, None] - vf[None], axis=2)
        mvd = float(max(dm.min(1).max(), dm.min(0).max()))
        st.append(FAIL if mvd > venue["tol"]["fence_match_m"] else PASS)
        det.append(f"file fence vs venue fence: max vertex difference {mvd:.2f} m ({len(ff)} vs {len(vf)} vertices); limit {venue['tol']['fence_match_m']} m")
    P = ctx["P"]
    flat = P[:, :, :2].reshape(-1, 2)
    margin = edge_dist(flat, vf)
    margin[~inside(flat, vf)] *= -1
    k = int(np.argmin(margin)); di, ki = divmod(k, P.shape[1])
    ok = margin[k] >= venue["fence_buffer"]
    st.append(PASS if ok else FAIL)
    det.append(f"closest approach to venue fence {margin[k]:.2f} m inside (negative = outside) vs buffer {venue['fence_buffer']:g} m - {_where(ctx, show, di, ki)}")
    if not ok:
        pts.append((*P[di, ki, :2], "fence"))
    summ = (f"fence match {mvd:.1f} m, " if mvd is not None else "no file fence, ") + f"min margin {margin[k]:.1f} m"
    amax = venue["limits"].get("max_altitude_m")
    if amax is not None:
        zi = np.unravel_index(np.argmax(P[:, :, 2]), P.shape[:2])
        za = float(P[zi[0], zi[1], 2])
        st.append(FAIL if za > amax else PASS)
        det.append(f"max altitude {za:.1f} m vs limit {amax:g} m - {_where(ctx, show, *zi)}")
        summ += f", max alt {za:.1f} m"
        if za > amax:
            pts.append((*P[zi[0], zi[1], :2], "altitude"))
    return Result("C3", "Geofence and altitude", worst(*st), summ, det, pts)


# ------------------------------------------------------------ C4
def c4_dynamics(show, venue, ctx):
    lim = venue["limits"]
    vmax, amax = lim.get("max_speed_mps"), lim.get("max_accel_mps2")
    P, t = ctx["P"], ctx["t"]
    v = np.diff(P, axis=1) * RATE_HZ                       # (D, T-1, 3)
    sp = np.linalg.norm(v, axis=2)
    acv = np.diff(v, axis=1) * RATE_HZ                     # (D, T-2, 3)
    ac = np.linalg.norm(acv, axis=2)
    s10 = {"speed": (sp.max(1), sp.argmax(1)), "acceleration": (ac.max(1), ac.argmax(1) + 1)}
    # file acceleration limits are per component (Skybrush maxAccelerationXY / maxAccelerationZ); the venue's is 3-D
    flim = getattr(show, "limits", None) or {}
    fxy, fz = flim.get("max_accel_xy"), flim.get("max_accel_z")
    use_file_acc = amax is None and (fxy is not None or fz is not None)
    exact = all(d.segments is not None for d in show.drones)
    det, pts, st = [], [], []
    if exact:                                               # analytic values from the Bezier segments (.skyc)
        from .dynamics import drone_extremes
        ex = [drone_extremes(d.segments) for d in show.drones]
        tidx = lambda tt: int(np.clip(np.searchsorted(t, tt), 0, len(t) - 1))
        sx = {"speed": (np.array([e["v"] for e in ex]), np.array([tidx(e["tv"]) for e in ex])),
              "acceleration": (np.array([e["a"] for e in ex]), np.array([tidx(e["ta"]) for e in ex]))}
        jumps = np.array([e["jump"] for e in ex])
        det.append("verdict uses EXACT values from the Bezier segments; the 10 Hz sampled value is shown in brackets")
    fdesc = " / ".join(f"{n} {x:g}" for n, x in (("maxAccelerationXY", fxy), ("maxAccelerationZ", fz)) if x is not None)
    if amax is not None:
        det.append(f"acceleration limit: venue limits.max_accel_mps2 {amax:g} m/s2 (3-D) used"
                   + (f"; file {fdesc} m/s2 not used (venue wins)" if fdesc else ""))
    elif use_file_acc:
        det.append(f"acceleration limit: file settings.validation {fdesc} m/s2 used (venue has no max_accel_mps2); "
                   "checked as horizontal and vertical parts")
    vgrade = show.grade != "flight"           # visualization-grade: acceleration is an estimate
    for name, lmt, unit in [("speed", vmax, "m/s"), ("acceleration", amax, "m/s2")]:
        peak10, k10 = s10[name]
        peak, kk = sx[name] if exact else s10[name]
        if lmt is None and name == "acceleration" and use_file_acc:
            for key, clim, label, a10 in [("axy", fxy, "horizontal", np.linalg.norm(acv[..., :2], axis=2)),
                                          ("az", fz, "vertical", np.abs(acv[..., 2]))]:
                if clim is None:
                    continue
                p10, k10c = a10.max(1), a10.argmax(1) + 1
                if exact:
                    pc = np.array([e[key] for e in ex]); kc = np.array([tidx(e["t" + key]) for e in ex])
                else:
                    pc, kc = p10, k10c
                bad = int((pc > clim).sum())
                st.append(PASS if bad == 0 else (WARN if vgrade else FAIL))
                i = int(np.argmax(pc))
                det.append(f"max {label} acceleration {pc.max():.3f} m/s2 [10 Hz: {p10.max():.3f}] vs limit {clim:g}; "
                           f"{bad} drone(s) over; worst {show.drones[i].id} @ {t[kc[i]]:.1f} s"
                           + (" (estimated from linear interpolation: corner spikes are artefacts, so WARN not FAIL)" if vgrade else ""))
                if bad and not vgrade:
                    for j in np.argsort(-pc)[:5]:
                        if pc[j] > clim:
                            pts.append((*P[j, kc[j], :2], f"{label} accel {pc[j]:.1f}"))
            continue
        if lmt is None:
            st.append(WARN); det.append(f"no {name} limit in venue; max {peak.max():.2f} {unit}"); continue
        order = np.argsort(-peak)[:5]
        bad = int((peak > lmt).sum())
        vis = show.grade != "flight" and name == "acceleration"
        st.append(PASS if bad == 0 else (WARN if vis else FAIL))
        det.append(f"max {name} {peak.max():.3f} {unit} [10 Hz: {peak10.max():.3f}] vs limit {lmt:g}; {bad} drone(s) over" +
                   (" (estimated from linear interpolation: corner spikes are artefacts, so WARN not FAIL)" if vis else ""))
        det.append(f"top 5 {name}: " + "; ".join(
            f"{show.drones[i].id} {peak[i]:.2f} [{peak10[i]:.2f}] @ {t[kk[i]]:.1f} s" for i in order))
        for i in order:
            if peak[i] > lmt and not vis:
                pts.append((*P[i, kk[i], :2], f"{name} {peak[i]:.1f}"))
    summ = (f"max speed {sx['speed'][0].max():.2f} m/s [10 Hz {sp.max():.2f}], max accel {sx['acceleration'][0].max():.2f} m/s2 "
            f"[10 Hz {ac.max():.2f}]") if exact else f"max speed {sp.max():.2f} m/s, max accel {ac.max():.2f} m/s2 (10 Hz)"
    if exact and amax is not None:
        # a velocity jump where two segments meet means unbounded acceleration; allow what amax gives in one 0.1 s tick
        jl = amax / RATE_HZ
        jb = np.where(jumps > jl)[0]
        st.append(FAIL if len(jb) else PASS)
        det.append(f"largest velocity jump at a segment join {jumps.max():.3f} m/s (allowed {jl:.2f} m/s = max accel x 0.1 s); "
                   f"{len(jb)} drone(s) over" + (": " + ", ".join(f"{show.drones[i].id} {jumps[i]:.2f} m/s @ {ex[i]['tj']:.1f} s"
                                                                     for i in jb[np.argsort(-jumps[jb])][:5]) if len(jb) else ""))
        summ += f", max join jump {jumps.max():.2f} m/s"
    elif exact and use_file_acc:
        # same rule per component: allow what the file's XY / Z limit gives in one 0.1 s tick
        for key, clim, label in [("jxy", fxy, "horizontal"), ("jz", fz, "vertical")]:
            if clim is None:
                continue
            jc, jl = np.array([e[key] for e in ex]), clim / RATE_HZ
            jb = np.where(jc > jl)[0]
            st.append(FAIL if len(jb) else PASS)
            det.append(f"largest {label} velocity jump at a segment join {jc.max():.3f} m/s (allowed {jl:.2f} m/s = "
                       f"file limit x 0.1 s); {len(jb)} drone(s) over" + (": " + ", ".join(
                           f"{show.drones[i].id} {jc[i]:.2f} m/s @ {ex[i]['t' + key]:.1f} s"
                           for i in jb[np.argsort(-jc[jb])][:5]) if len(jb) else ""))
        summ += f", max join jump {jumps.max():.2f} m/s"
    return Result("C4", "Speed and acceleration", worst(*st), summ, det, pts)


# ------------------------------------------------------------ C5
def c5_spacing(show, venue, ctx):
    lim = venue["limits"].get("min_spacing_m")
    P, t = ctx["P"], ctx["t"]
    D, T = P.shape[:2]
    if D < 2:
        return Result("C5", "Closest pair", PASS, "single drone", [])
    # [DOC] Skybrush minNavAltitude: below it, neighbour distances are not checked (drones on or near the pads)
    nav, nav_src = pick_limit(show, venue, "min_nav_altitude_m", "min_nav_altitude_m", "minNavAltitude")
    air = None if nav is None else (P[:, :, 2] - ctx["dz"]) >= nav     # (D, T): show Z above the show origin
    best = (np.inf, 0, 0, 0)
    step = max(1, int(4e6 // (D * D)))
    iu = np.triu_indices(D, 1)
    for k0 in range(0, T, step):
        X = P[:, k0:k0 + step].transpose(1, 0, 2)            # (s, D, 3)
        dd = np.linalg.norm(X[:, :, None] - X[:, None], axis=3)[:, iu[0], iu[1]]
        if air is not None:
            A = air[:, k0:k0 + step].T                         # (s, D)
            dd = np.where(A[:, iu[0]] & A[:, iu[1]], dd, np.inf)
        j = np.unravel_index(np.argmin(dd), dd.shape)
        if dd[j] < best[0]:
            best = (float(dd[j]), k0 + j[0], iu[0][j[1]], iu[1][j[1]])
    if air is None:
        nav_det = "no minNavAltitude in the file or venue: every pair checked at every 10 Hz frame"
    else:
        n_air = air.sum(0)
        total, checked = T * (D * (D - 1) // 2), int((n_air * (n_air - 1) // 2).sum())
        nav_det = (f"minNavAltitude {nav:g} m from {nav_src}: skipped {total - checked:,} of {total:,} pair-samples "
                   f"in {int((n_air < D).sum())} of {T} frames (10 Hz), because one or both drones were below {nav:g} m "
                   f"above the show origin (Skybrush does not check neighbour distances there)")
    d, k, a, b = best
    if not np.isfinite(d):
        return Result("C5", "Closest pair", WARN, f"no pair above minNavAltitude {nav:g} m: nothing checked",
                      [nav_det], [])
    ok = lim is None or d >= lim
    det = [f"closest pair {show.drones[a].id} / {show.drones[b].id}: {d:.2f} m (3-D) at t={t[k]:.1f} s, "
           f"E {P[a, k, 0]:.1f} N {P[a, k, 1]:.1f} alt {P[a, k, 2]:.1f} m" + (f"; limit {lim:g} m" if lim else "; no limit in venue"),
           nav_det]
    pts = [] if ok else [((P[a, k, 0] + P[b, k, 0]) / 2, (P[a, k, 1] + P[b, k, 1]) / 2, f"pair {d:.2f} m")]
    st = WARN if lim is None else (PASS if ok else FAIL)
    return Result("C5", "Closest pair", st, f"{d:.2f} m ({show.drones[a].id}/{show.drones[b].id} @ {t[k]:.1f} s)", det, pts)


def run_checks(show, venue, expected_manifest=None):
    from .fingerprint import c6_result
    ctx = prepare(show, venue)
    res = [f(show, venue, ctx) for f in (c1_origin, c2_crowd, c3_fence, c4_dynamics, c5_spacing)]
    res.append(c6_result(show, expected_manifest))
    return res, ctx
