# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 forgottenl5
"""Checks C1-C5 (C6 is in fingerprint.py). Each returns a Result with PASS / FAIL / WARN, the numbers, and where/when."""
from dataclasses import dataclass, field
import numpy as np
from .model import sample, local_to_venue, heading_diff, geo_offset, RATE_HZ

PASS, WARN, FAIL = "PASS", "WARN", "FAIL"
DEFAULT_MIN_NAV_ALTITUDE_M = 2.5        # Skybrush firmware SHOW_TAKEOFF_ALT default, used when no file/venue value


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
    # check window (C2-C5): each drone up to min(end of trajectory, landingTime), see readers.read_skyc
    te = np.array([getattr(d, "window_end", d.t_end) for d in show.drones])
    valid = t[None, :] <= te[:, None] + 1e-9                  # (D, T)
    return {"t": t, "P": Pv, "homes": homes, "fence": fence, "origin": origin, "heading": heading, "dz": dz,
            "valid": valid, "t_end": te}


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
        d[~ctx["valid"].reshape(-1)] = np.inf
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
    margin[~ctx["valid"].reshape(-1)] = np.inf
    k = int(np.argmin(margin)); di, ki = divmod(k, P.shape[1])
    ok = margin[k] >= venue["fence_buffer"]
    st.append(PASS if ok else FAIL)
    det.append(f"closest approach to venue fence {margin[k]:.2f} m inside (negative = outside) vs buffer {venue['fence_buffer']:g} m - {_where(ctx, show, di, ki)}")
    if not ok:
        pts.append((*P[di, ki, :2], "fence"))
    summ = (f"fence match {mvd:.1f} m, " if mvd is not None else "no file fence, ") + f"min margin {margin[k]:.1f} m"
    amax = venue["limits"].get("max_altitude_m")
    if amax is not None:
        zi = np.unravel_index(np.argmax(np.where(ctx["valid"], P[:, :, 2], -np.inf)), P.shape[:2])
        za = float(P[zi[0], zi[1], 2])
        st.append(FAIL if za > amax else PASS)
        det.append(f"max altitude {za:.1f} m vs limit {amax:g} m - {_where(ctx, show, *zi)}")
        summ += f", max alt {za:.1f} m"
        if za > amax:
            pts.append((*P[zi[0], zi[1], :2], "altitude"))
    return Result("C3", "Geofence and altitude", worst(*st), summ, det, pts)


# ------------------------------------------------------------ C4
def _judge(name, unit, peak, tk, limit, src, level, show, ctx, det, pts, st, top=True):
    """One quantity (per-drone peaks, 10 Hz frame index of each) against one limit; `level` (FAIL/WARN) when over."""
    bad = int((peak > limit).sum())
    st.append(PASS if bad == 0 else level)
    i = int(np.argmax(peak))
    det.append(f"max {name} {peak.max():.3f} {unit} vs {src} {limit:g}: {bad} drone(s) over"
               + (f" ({level})" if bad else "") + f"; worst {show.drones[i].id} @ {ctx['t'][tk[i]]:.1f} s")
    if top:
        det.append(f"top 5 {name}: " + "; ".join(f"{show.drones[j].id} {peak[j]:.2f} @ {ctx['t'][tk[j]]:.1f} s"
                                                 for j in np.argsort(-peak)[:5]))
    if level == FAIL:
        for j in np.argsort(-peak)[:5]:
            if peak[j] > limit:
                pts.append((*ctx["P"][j, tk[j], :2], f"{name} {peak[j]:.1f}"))


def c4_dynamics(show, venue, ctx):
    """Speed and acceleration.

    Venue limits (limits.max_speed_mps, limits.max_accel_mps2, both 3-D) can FAIL. The file's settings.validation
    levels (maxVelocityXY / Z / ZUp, maxAccelerationXY / Z) are the designer's warning levels: they only ever WARN.
    """
    from .dynamics import drone_speeds, acceleration_peaks, CTRL_RATE_HZ
    lim, flim = venue["limits"], getattr(show, "limits", None) or {}
    rate = float(ctx.get("ctrl_rate", CTRL_RATE_HZ))
    P, t = ctx["P"], ctx["t"]
    tidx = lambda tt: int(np.clip(np.round((tt - t[0]) * RATE_HZ), 0, len(t) - 1))
    vgrade = show.grade != "flight"           # visualization-grade (.vviz): linear path, its corners are artefacts
    det, pts, st = [], [], []

    # speed: derivative of the Bezier segments (.skyc); 10 Hz differences of the linear path (.vviz)
    if all(d.segments is not None for d in show.drones):
        ex = [drone_speeds(d.segments, te) for d, te in zip(show.drones, ctx["t_end"])]
        spd = {k: (np.array([e[k] for e in ex]), np.array([tidx(e["t" + k]) for e in ex]))
               for k in ("v", "vxy", "vup", "vdown")}
        det.append("speed from the derivative of the Bezier segments")
    else:
        v = np.diff(P, axis=1) * RATE_HZ * (ctx["valid"][:, 1:] & ctx["valid"][:, :-1])[..., None]
        mags = {"v": np.linalg.norm(v, axis=2), "vxy": np.linalg.norm(v[..., :2], axis=2),
                "vup": np.maximum(v[..., 2], 0), "vdown": np.maximum(-v[..., 2], 0)}
        spd = {k: (m.max(1), m.argmax(1) + 1) for k, m in mags.items()}
        det.append("speed from 10 Hz differences of the linearly interpolated path")

    # acceleration: the path sampled at the controller rate, central differences (see dynamics.py)
    ac = [acceleration_peaks(d.at, d.t_start, te, rate, float(t[0])) for d, te in zip(show.drones, ctx["t_end"])]
    acc = {k: (np.array([e[k] for e in ac]), np.array([tidx(e["t" + k]) for e in ac])) for k in ("a", "axy", "az")}
    det.append(f"acceleration from the path sampled at {rate:g} Hz (the firmware's show control rate), central "
               "differences for velocity and again for acceleration"
               + ("; visualization-grade file: corners of the linear path read as spikes, so acceleration only WARNs"
                  if vgrade else ""))

    # venue limits: these can FAIL
    for name, unit, pk, limit, src, level in [
            ("speed", "m/s", spd["v"], lim.get("max_speed_mps"), "venue limits.max_speed_mps", FAIL),
            ("acceleration", "m/s2", acc["a"], lim.get("max_accel_mps2"), "venue limits.max_accel_mps2",
             WARN if vgrade else FAIL)]:
        if limit is None:
            st.append(WARN)
            det.append(f"no {name} limit in venue: max {pk[0].max():.2f} {unit} not verified")
        else:
            _judge(name, unit, *pk, float(limit), src, level, show, ctx, det, pts, st)

    # file levels (designer warnings): WARN only, never FAIL
    up_key = "max_speed_z_up" if "max_speed_z_up" in flim else "max_speed_z"
    for name, unit, pk, key, fname in [
            ("horizontal speed", "m/s", spd["vxy"], "max_speed_xy", "maxVelocityXY"),
            ("climb rate", "m/s", spd["vup"], up_key, "maxVelocityZUp" if up_key == "max_speed_z_up" else "maxVelocityZ"),
            ("descent rate", "m/s", spd["vdown"], "max_speed_z", "maxVelocityZ"),
            ("horizontal acceleration", "m/s2", acc["axy"], "max_accel_xy", "maxAccelerationXY"),
            ("vertical acceleration", "m/s2", acc["az"], "max_accel_z", "maxAccelerationZ")]:
        if flim.get(key) is not None:
            _judge(name, unit, *pk, float(flim[key]), f"file settings.validation.{fname} (designer warning level)",
                   WARN, show, ctx, det, pts, st, top=False)

    summ = (f"max speed {spd['v'][0].max():.2f} m/s, max accel {acc['a'][0].max():.2f} m/s2 (accel: {rate:g} Hz sampled "
            "positions, central difference of central-difference velocity; values depend on rate (--ctrl-rate))")
    return Result("C4", "Speed and acceleration", worst(*st), summ, det, pts)


# ------------------------------------------------------------ C5
def c5_spacing(show, venue, ctx):
    lim = venue["limits"].get("min_spacing_m")                          # venue limit: can FAIL
    flim = (getattr(show, "limits", None) or {}).get("min_spacing_m")   # file minDistance: designer warning level
    P, t = ctx["P"], ctx["t"]
    D, T = P.shape[:2]
    if D < 2:
        return Result("C5", "Closest pair", PASS, "single drone", [])
    # [DOC] Skybrush minNavAltitude: below it, neighbour distances are not checked (drones on or near the pads)
    nav, nav_src = pick_limit(show, venue, "min_nav_altitude_m", "min_nav_altitude_m", "minNavAltitude")
    if nav is None:
        nav, nav_src = DEFAULT_MIN_NAV_ALTITUDE_M, "the default (Skybrush SHOW_TAKEOFF_ALT; neither file nor venue sets it)"
    air = ((P[:, :, 2] - ctx["dz"]) >= nav) & ctx["valid"]    # (D, T): show Z above the show origin, in the window
    best = (np.inf, 0, 0, 0)
    step = max(1, int(4e6 // (D * D)))
    iu = np.triu_indices(D, 1)
    for k0 in range(0, T, step):
        X = P[:, k0:k0 + step].transpose(1, 0, 2)            # (s, D, 3)
        dd = np.linalg.norm(X[:, :, None] - X[:, None], axis=3)[:, iu[0], iu[1]]
        A = air[:, k0:k0 + step].T                             # (s, D)
        dd = np.where(A[:, iu[0]] & A[:, iu[1]], dd, np.inf)
        j = np.unravel_index(np.argmin(dd), dd.shape)
        if dd[j] < best[0]:
            best = (float(dd[j]), k0 + j[0], iu[0][j[1]], iu[1][j[1]])
    n_air = air.sum(0)
    total, checked = T * (D * (D - 1) // 2), int((n_air * (n_air - 1) // 2).sum())
    nav_det = (f"minNavAltitude {nav:g} m from {nav_src}: skipped {total - checked:,} of {total:,} pair-samples "
               f"in {int((n_air < D).sum())} of {T} frames (10 Hz), because one or both drones were below {nav:g} m "
               f"above the show origin (Skybrush does not check neighbour distances there) or past the end of "
               f"their check window (landingTime)")
    d, k, a, b = best
    if not np.isfinite(d):
        return Result("C5", "Closest pair", WARN, f"no pair above minNavAltitude {nav:g} m: nothing checked",
                      [nav_det], [])
    det = [f"closest pair {show.drones[a].id} / {show.drones[b].id}: {d:.2f} m (3-D) at t={t[k]:.1f} s, "
           f"E {P[a, k, 0]:.1f} N {P[a, k, 1]:.1f} alt {P[a, k, 2]:.1f} m", nav_det]
    st = []
    if lim is None:
        st.append(WARN); det.append("no min_spacing_m in venue: closest pair not verified")
    else:
        st.append(PASS if d >= lim else FAIL)
        det.append(f"venue limits.min_spacing_m {lim:g} m: " + ("ok" if d >= lim else "FAIL"))
    if flim is not None:
        st.append(PASS if d >= flim else WARN)
        det.append(f"file settings.validation.minDistance {flim:g} m (designer warning level): "
                   + ("ok" if d >= flim else "WARN"))
    pts = [] if lim is None or d >= lim else [((P[a, k, 0] + P[b, k, 0]) / 2, (P[a, k, 1] + P[b, k, 1]) / 2, f"pair {d:.2f} m")]
    return Result("C5", "Closest pair", worst(*st), f"{d:.2f} m ({show.drones[a].id}/{show.drones[b].id} @ {t[k]:.1f} s)",
                  det, pts)


def run_checks(show, venue, expected_manifest=None, ctrl_rate=None):
    """Run C1-C6. ctrl_rate: Hz at which C4 samples the path for acceleration (default dynamics.CTRL_RATE_HZ, 10)."""
    from .fingerprint import c6_result
    from .dynamics import CTRL_RATE_HZ, CTRL_RATE_RANGE
    ctx = prepare(show, venue)
    ctx["ctrl_rate"] = CTRL_RATE_HZ if ctrl_rate is None else float(ctrl_rate)
    lo, hi = CTRL_RATE_RANGE
    if not lo <= ctx["ctrl_rate"] <= hi:
        raise ValueError(f"control rate {ctx['ctrl_rate']:g} Hz is outside {lo:g}-{hi:g} Hz (Skybrush SHOW_CTRL_RATE range)")
    res = [f(show, venue, ctx) for f in (c1_origin, c2_crowd, c3_fence, c4_dynamics, c5_spacing)]
    res.append(c6_result(show, expected_manifest))
    return res, ctx
