# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 forgottenl5
"""Readers for .skyc (Skybrush compiled, ZIP) and .vviz (Verge/Finale JSON) -> model.Show.

Written from FORMAT_NOTES.md; .skyc axes, origin and time conventions confirmed by a real Skybrush Studio export.
"""
import numpy as np
from .model import Drone, Show
from .limits import LIMITS, SafeZip, LimitError, load_json_file, check_count, check_show, need, finite


# ---------------------------------------------------------------- .skyc
def _bezier(p0, cps, p1, u):
    """Point on a straight / quadratic / cubic Bezier at u (T,) -> (T, 3)."""
    u = u[:, None]
    v = 1.0 - u
    if len(cps) == 0:
        return v * p0 + u * p1
    if len(cps) == 1:
        return v * v * p0 + 2 * v * u * cps[0] + u * u * p1
    if len(cps) == 2:
        return v**3 * p0 + 3 * v * v * u * cps[0] + 3 * v * u * u * cps[1] + u**3 * p1
    raise ValueError(f"segment with {len(cps)} control points (only 0, 1 or 2 are valid)")


def _trajectory_fn(points, shift):
    T = np.array([float(p[0]) for p in points]) + shift
    P = np.array([p[1] for p in points], float)
    C = [np.array(p[2], float).reshape(-1, 3) if len(p) > 2 and p[2] else np.zeros((0, 3)) for p in points]
    if P.ndim != 2 or P.shape[1] != 3:
        raise LimitError("trajectory points must be [time, [x, y, z], control points]")
    if not (np.all(np.isfinite(T)) and np.all(np.isfinite(P)) and all(np.all(np.isfinite(c)) for c in C)):
        raise LimitError("trajectory has non-finite times or coordinates")
    if np.any(np.diff(T) < 0):
        raise ValueError("trajectory times are not increasing")

    def at(t):
        t = np.asarray(t, float)
        out = np.empty((len(t), 3))
        out[t <= T[0]] = P[0]
        out[t >= T[-1]] = P[-1]
        k = np.searchsorted(T, t, side="right")          # segment k: points k-1 -> k, control points of point k
        for s in np.unique(k[(t > T[0]) & (t < T[-1])]):
            m = (k == s) & (t > T[0]) & (t < T[-1])
            dt = T[s] - T[s - 1]
            u = (t[m] - T[s - 1]) / dt if dt > 0 else np.ones(m.sum())
            out[m] = _bezier(P[s - 1], C[s], P[s], u)
        return out
    segs = [(T[s - 1], T[s], np.vstack([P[s - 1], C[s], P[s]])) for s in range(1, len(T))]
    return at, T[0], T[-1], segs


def _resolve(z, obj):
    """Follow a {"$ref": "./drones/x/trajectory.json#"} link inside the ZIP (never outside it)."""
    if isinstance(obj, dict) and "$ref" in obj:
        return z.json(z.ref(obj["$ref"]))
    return obj


def _light_length(lights):
    """Decoded bytecode bytes of a {version, data: "<base64>"} light program."""
    data = lights.get("data") if isinstance(lights, dict) else None
    return len(data) * 3 // 4 if isinstance(data, str) else 0


def read_skyc(path, limits=None) -> Show:
    lim = LIMITS if limits is None else limits
    with SafeZip(path, lim) as z:
        spec = z.json("show.json")
        need(isinstance(spec, dict), "show.json is not a JSON object")
        if spec.get("version") != 1:
            raise ValueError(f"unsupported .skyc show.json version {spec.get('version')!r}")
        notes = []
        env = spec.get("environment") or {}
        settings = spec.get("settings") or {}
        origin = heading = None
        handed = "nwu"
        loc = env.get("location")
        if loc and loc.get("origin"):
            o = loc["origin"]
            if all(isinstance(v, int) for v in o[:2]):   # integers: deg x 1e7, altitude in mm [confirmed by real export]
                origin = (o[0] / 1e7, o[1] / 1e7, (o[2] / 1e3) if len(o) > 2 and o[2] is not None else 0.0)
                if len(o) < 3 or o[2] is None:
                    notes.append("origin has no altitude in the file; 0 m assumed (Skybrush Studio exports omit it).")
            else:                                      # already in degrees / metres
                origin = (float(o[0]), float(o[1]), float(o[2]) if len(o) > 2 and o[2] is not None else 0.0)
                notes.append("environment.location.origin looked like plain degrees, not deg x 1e7; read as degrees.")
            heading = float(loc.get("orientation", 0.0))
        cs = spec.get("coordinateSystem") or settings.get("coordinateSystem")
        if origin is None and isinstance(cs, dict) and cs.get("origin"):
            lon, lat = cs["origin"][:2]                # server-side order is (longitude, latitude)
            origin, heading = (float(lat), float(lon), 0.0), float(cs.get("orientation", 0.0))
            notes.append("origin taken from coordinateSystem (lon, lat order).")
        if isinstance(cs, dict) and cs.get("type") in ("nwu", "neu"):
            handed = cs["type"]
        else:
            notes.append("axis handedness not stored in .skyc; NWU-type (x = orientation, y = 90 deg left of x, z up), "
                         "as Skybrush Studio writes Blender's right-handed X/Y/Z unchanged [confirmed by real export].")
        if origin is None:
            notes.append("no show origin in file; venue origin will be assumed (C1 cannot verify it).")

        fence = None
        gf = settings.get("geofence")
        if gf and gf.get("polygons"):
            inc = [p for p in gf["polygons"] if p.get("isInclusion", True)]
            if inc:
                fence = np.array([pt[:2] for pt in inc[0]["points"]], float)
            if len(inc) > 1 or len(inc) < len(gf["polygons"]):
                notes.append("geofence has several or exclusion polygons; only the first inclusion polygon is checked.")
        if fence is None:
            notes.append("no geofence in the file (Skybrush Studio has no geofence setting); C3 checks the venue fence only.")
        val = settings.get("validation") or {}
        limits = {k2: float(val[k1]) for k1, k2 in [("maxVelocityXY", "max_speed_xy"), ("maxVelocityZ", "max_speed_z"),
                                                    ("maxVelocityZUp", "max_speed_z_up"), ("minDistance", "min_spacing_m"),
                                                    ("maxAltitude", "max_altitude_m")] if val.get(k1) is not None}

        drones, shifted, total_points = [], False, 0
        swarm = spec["swarm"]["drones"]
        need(isinstance(swarm, list), "swarm.drones is not a list")
        check_count(len(swarm), "max_drones", "drones in the show", lim)
        for i, d in enumerate(swarm):
            s = d.get("settings", {})
            traj = _resolve(z, s["trajectory"])
            need(isinstance(traj, dict) and isinstance(traj.get("points"), list) and len(traj["points"]) > 0,
                 f"drone {i}: trajectory has no points list")
            if traj.get("version", 1) != 1:
                raise ValueError(f"drone {i}: unsupported trajectory version {traj.get('version')!r}")
            check_count(len(traj["points"]) - 1, "max_segments", f"drone {i}: trajectory segments", lim)
            total_points += len(traj["points"])
            check_count(total_points, "max_points", "trajectory points in the show", lim)
            shift = finite(traj.get("takeoffTime") or 0.0, f"drone {i}: takeoffTime")
            shifted |= shift != 0
            at, ts, te, segs = _trajectory_fn(traj["points"], shift)
            home = np.array(s["home"], float) if s.get("home") else at(np.array([ts]))[0]
            raw = {"trajectory": traj, "lights": _resolve(z, s.get("lights"))}
            check_count(_light_length(raw["lights"]), "max_light_length", f"drone {i}: light program bytes", lim)
            if s.get("pyro") is not None:
                raw["pyro"] = _resolve(z, s["pyro"])
            drones.append(Drone(str(s.get("name", i)), home, at, ts, te,
                                traj.get("takeoffTime"), traj.get("landingTime"), segs, raw))
        if shifted:
            notes.append("trajectory times shifted by takeoffTime (show time = t + takeoffTime) [confirmed by real export].")
        notes.append("trajectories evaluated exactly from the Bezier segments, sampled at 10 Hz.")
    show = Show(str(path), "skyc", "flight", drones, origin, heading, handed, fence, limits, notes)
    check_show(show, lim)
    return show


# ---------------------------------------------------------------- .vviz
def read_vviz(path, limits=None) -> Show:
    lim = LIMITS if limits is None else limits
    spec = load_json_file(path, lim)
    need(isinstance(spec, dict) and isinstance(spec.get("performances"), list), "not a .vviz file (no performances list)")
    rate = finite(spec.get("defaultPositionRate", 1.0), "defaultPositionRate")
    need(rate > 0, "defaultPositionRate must be positive")
    off = finite(spec.get("timeOffsetSecs", 0.0), "timeOffsetSecs")
    g = spec.get("globalReferenceFrame") or {}
    origin = (float(g["lat"]), float(g["lon"]), float(g.get("alt", 0.0))) if "lat" in g and "lon" in g else None
    drones, total_points = [], 0
    check_count(len(spec["performances"]), "max_drones", "drones in the show", lim)
    for p in spec["performances"]:
        a = p["agentDescription"]
        steps = a.get("agentTraversal", [])
        need(isinstance(steps, list), f"drone {len(drones)}: agentTraversal is not a list")
        check_count(len(steps), "max_segments", f"drone {len(drones)}: trajectory steps", lim)
        total_points += len(steps) + 1
        check_count(total_points, "max_points", "trajectory points in the show", lim)
        pay = p.get("payloadDescription")
        n_act = sum(len(x.get("payloadActions") or []) for x in pay if isinstance(x, dict)) if isinstance(pay, list) else 0
        check_count(n_act, "max_light_length", f"drone {len(drones)}: light payload actions", lim)
        t = off + np.concatenate([[0.0], np.cumsum([s["dt"] if "dt" in s else 1.0 / rate for s in steps])])
        d = np.array([[s.get("dx", 0.0), s.get("dy", 0.0), s.get("dz", 0.0)] for s in steps]).reshape(-1, 3)
        ogl = np.cumsum(np.vstack([[a.get("homeX", 0.0), a.get("homeY", 0.0), a.get("homeZ", 0.0)], d]), 0)
        pos = ogl[:, [0, 2, 1]]                        # ogl X right, Y up, Z forward -> local x, y(forward), z(up)
        need(bool(np.all(np.isfinite(t)) and np.all(np.isfinite(pos))), f"drone {len(drones)}: non-finite time or position")
        need(bool(np.all(np.diff(t) >= 0)), f"drone {len(drones)}: negative dt (time goes backwards)")

        def at(tq, t=t, pos=pos):
            return np.stack([np.interp(tq, t, pos[:, k]) for k in range(3)], 1)
        up = np.where(pos[:, 2] > pos[0, 2] + 0.5)[0]
        drones.append(Drone(str(p.get("id", len(drones))), pos[0].copy(), at, t[0], t[-1],
                            float(t[up[0]]) if len(up) else None, float(t[up[-1]]) if len(up) else None,
                            None, {"trajectory": a, "lights": p.get("payloadDescription")}))
    notes = ["VISUALIZATION-GRADE file (Verge: VVIZ is not flight-ready): heading and geofence are not in the file and "
             "are taken from the venue file, so C1 cannot verify the heading.",
             "positions between samples are LINEARLY interpolated (2 Hz source is typical); acceleration is an estimate.",
             "axes: ogl X = local x (right), ogl Z = local y (forward), ogl Y = up [GUESS for the horizontal mapping]."]
    show = Show(str(path), "vviz", "visualization", drones, origin, None, "nwu", None, {}, notes)
    check_show(show, lim)
    return show


def read_show(path, limits=None) -> Show:
    """Read a .skyc or .vviz. Any malformed or over-limit file raises LimitError with a one-line reason."""
    p = str(path).lower()
    if p.endswith(".skyc"):
        reader = read_skyc
    elif p.endswith(".vviz"):
        reader = read_vviz
    else:
        raise LimitError(f"unknown show file type: {path} (expected .skyc or .vviz)")
    try:
        return reader(path, limits)
    except LimitError:
        raise
    except MemoryError:
        raise LimitError("ran out of memory reading the show file") from None
    except (KeyError, IndexError, TypeError, ValueError, AttributeError, OverflowError, ZeroDivisionError) as e:
        what = f"missing field {e}" if isinstance(e, KeyError) else f"{type(e).__name__}: {str(e)[:200]}"
        raise LimitError(f"malformed show file ({what})") from None
