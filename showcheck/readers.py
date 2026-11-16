# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 forgottenl5
"""Readers for .skyc (Skybrush compiled, ZIP) and .vviz (Verge/Finale JSON) -> model.Show.

Written from FORMAT_NOTES.md; .skyc axes, origin and time conventions confirmed by a real Skybrush Studio export.
"""
import json
import zipfile
import numpy as np
from .model import Drone, Show


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
    """Follow a {"$ref": "./drones/x/trajectory.json#"} link inside the ZIP."""
    if isinstance(obj, dict) and "$ref" in obj:
        name = obj["$ref"].split("#")[0].lstrip("./").replace("\\", "/")
        return json.loads(z.read(name))
    return obj


def read_skyc(path) -> Show:
    with zipfile.ZipFile(path) as z:
        spec = json.loads(z.read("show.json"))
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

        drones, shifted = [], False
        for i, d in enumerate(spec["swarm"]["drones"]):
            s = d.get("settings", {})
            traj = _resolve(z, s["trajectory"])
            if traj.get("version", 1) != 1:
                raise ValueError(f"drone {i}: unsupported trajectory version {traj.get('version')!r}")
            shift = float(traj.get("takeoffTime") or 0.0)
            shifted |= shift != 0
            at, ts, te, segs = _trajectory_fn(traj["points"], shift)
            home = np.array(s["home"], float) if s.get("home") else at(np.array([ts]))[0]
            raw = {"trajectory": traj, "lights": _resolve(z, s.get("lights"))}
            if s.get("pyro") is not None:
                raw["pyro"] = _resolve(z, s["pyro"])
            drones.append(Drone(str(s.get("name", i)), home, at, ts, te,
                                traj.get("takeoffTime"), traj.get("landingTime"), segs, raw))
        if shifted:
            notes.append("trajectory times shifted by takeoffTime (show time = t + takeoffTime) [confirmed by real export].")
        notes.append("trajectories evaluated exactly from the Bezier segments, sampled at 10 Hz.")
    return Show(str(path), "skyc", "flight", drones, origin, heading, handed, fence, limits, notes)


# ---------------------------------------------------------------- .vviz
def read_vviz(path) -> Show:
    with open(path, encoding="utf-8-sig") as f:
        spec = json.load(f)
    rate = float(spec.get("defaultPositionRate", 1.0))
    off = float(spec.get("timeOffsetSecs", 0.0))
    g = spec.get("globalReferenceFrame") or {}
    origin = (float(g["lat"]), float(g["lon"]), float(g.get("alt", 0.0))) if "lat" in g and "lon" in g else None
    drones = []
    for p in spec["performances"]:
        a = p["agentDescription"]
        steps = a.get("agentTraversal", [])
        t = off + np.concatenate([[0.0], np.cumsum([s["dt"] if "dt" in s else 1.0 / rate for s in steps])])
        d = np.array([[s.get("dx", 0.0), s.get("dy", 0.0), s.get("dz", 0.0)] for s in steps]).reshape(-1, 3)
        ogl = np.cumsum(np.vstack([[a.get("homeX", 0.0), a.get("homeY", 0.0), a.get("homeZ", 0.0)], d]), 0)
        pos = ogl[:, [0, 2, 1]]                        # ogl X right, Y up, Z forward -> local x, y(forward), z(up)

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
    return Show(str(path), "vviz", "visualization", drones, origin, None, "nwu", None, {}, notes)


def read_show(path) -> Show:
    p = str(path).lower()
    if p.endswith(".skyc"):
        return read_skyc(path)
    if p.endswith(".vviz"):
        return read_vviz(path)
    raise ValueError(f"unknown show file type: {path}")
