# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 forgottenl5
"""Synthetic test show (our own, freely redistributable) written as a .skyc-shaped ZIP, plus its matching venue.

100 drones on pads laid out as a polar grid (4 rings x 25), each ring climbs to its own transit altitude
(10/13/16/19 m), moves radially out to one 40 m circle, rises to 30 m, holds, and comes back the same way.
Rings never share an altitude while crossing, so the clean show keeps >= 2.5 m spacing.
Every segment is a cubic Bezier with zero velocity at both ends (control points = end points).
"""
import json
import math
import zipfile
import numpy as np
from .model import local_to_venue, venue_to_local, offset_latlon

VENUE_ORIGIN = (46.5, 7.5, 500.0)       # synthetic location (lat, lon, alt m)
HEADING = 30.0                           # show x+ axis, degrees from North toward East
R_CIRCLE, ALT_SHOW = 40.0, 30.0
CROWD = [[-60.0, -160.0], [60.0, -160.0], [60.0, -100.0], [-60.0, -100.0]]   # south of the show (venue ENU)
FENCE = [[-80.0, -80.0], [80.0, -80.0], [80.0, 80.0], [-80.0, 80.0]]


def layout():
    """Per drone: (home xy, ring radius, angle, transit altitude) in show-local metres."""
    out = []
    for i in range(100):
        k, j = i % 4, i // 4
        r, a, h = 12.0 + 3 * k, math.radians(3.6 * i), 10.0 + 3 * k
        out.append((np.array([r * math.cos(a), r * math.sin(a)]), r, a, h))
    return out


def keyframes(i, scenario):
    _, r, a, h = layout()[i]
    c, s = math.cos(a), math.sin(a)
    P = lambda rad, z: [rad * c, rad * s, z]
    kf = [(0, P(r, 0)), (10, P(r, h)), (20, P(R_CIRCLE, h)), (28, P(R_CIRCLE, ALT_SHOW))]
    if scenario == "speed_spike" and i == 0:          # 4 m radial jump in 0.3 s and back (linear segments)
        kf += [(33.0, P(R_CIRCLE, ALT_SHOW)), (33.3, P(R_CIRCLE + 4, ALT_SHOW), "lin"), (33.6, P(R_CIRCLE, ALT_SHOW), "lin")]
    if scenario == "near_pass" and i == 0:            # drone 0 moves 1.8 m toward drone 1 (2.51 m away) and back
        b = math.radians(3.6)
        tgt = np.array(P(R_CIRCLE, ALT_SHOW)); nb = np.array([R_CIRCLE * math.cos(b), R_CIRCLE * math.sin(b), ALT_SHOW])
        near = tgt + (nb - tgt) / np.linalg.norm(nb - tgt) * 1.8
        kf += [(32, list(tgt)), (34, list(near)), (36, list(near)), (38, list(tgt))]
    kf += [(40, P(R_CIRCLE, ALT_SHOW)), (48, P(R_CIRCLE, h)), (58, P(r, h)), (68, P(r, 0))]
    return kf


def trajectory(i, scenario):
    pts, prev = [], None
    for f in keyframes(i, scenario):
        t, p = f[0], [round(x, 4) for x in f[1]]
        cps = [] if prev is None or (len(f) > 2 and f[2] == "lin") else [prev, p]   # ease in/out: cps = end points
        pts.append([t, p, cps]); prev = p
    return {"version": 1, "points": pts, "takeoffTime": 0, "landingTime": 68}


def venue_dict():
    homes = np.array([h for h, *_ in layout()])
    pads = local_to_venue(homes, VENUE_ORIGIN, HEADING, "nwu", VENUE_ORIGIN)
    return {"name": "Synthetic test venue (not a real site)",
            "origin": {"lat": VENUE_ORIGIN[0], "lon": VENUE_ORIGIN[1], "alt": VENUE_ORIGIN[2]},
            "heading_deg": HEADING,
            "tolerances": {"origin_m": 2.0, "heading_deg": 2.0, "pad_m": 1.0, "fence_match_m": 1.0},
            "pads": [[round(e, 3), round(n, 3)] for e, n in pads],
            "pad_assignment": {f"drone_{i + 1:03d}": i for i in range(len(pads))},
            "crowd_areas": [{"name": "south crowd", "polygon": CROWD, "buffer_m": 50.0}],
            "geofence": {"polygon": FENCE, "buffer_m": 5.0},
            "limits": {"max_speed_mps": 8.0, "max_accel_mps2": 4.0, "min_spacing_m": 2.0, "max_altitude_m": 120.0}}


def write_skyc(path, scenario="clean"):
    """scenario: clean | shift18 | heading7 | stale_fence | speed_spike | near_pass | pad_swap"""
    lat, lon, alt = VENUE_ORIGIN
    heading = HEADING + (7.0 if scenario == "heading7" else 0.0)
    if scenario == "shift18":                          # 18 m toward the crowd (south)
        lat, lon = offset_latlon(lat, lon, 0.0, -18.0)
    fence_enu = np.array(FENCE) * (70 / 80 if scenario == "stale_fence" else 1.0)
    # the fence was designed for the planned placement, so it is stored relative to the venue origin + heading
    fence_local = venue_to_local(fence_enu, VENUE_ORIGIN, HEADING, "nwu", VENUE_ORIGIN)
    show = {"version": 1,
            "meta": {"title": f"ShowCheck synthetic test show ({scenario})"},
            "environment": {"type": "outdoor",
                            "location": {"origin": [round(lat * 1e7), round(lon * 1e7), round(alt * 1e3)],
                                         "orientation": heading}},
            "settings": {"geofence": {"version": 1, "action": "land",
                                      "polygons": [{"isInclusion": True, "points": [[round(x, 3), round(y, 3), 0.0] for x, y in fence_local]}]},
                         "validation": {"maxAltitude": 120, "maxVelocityXY": 8, "maxVelocityZ": 4, "minDistance": 2.0}},
            "swarm": {"drones": []}}
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for i, (home, *_) in enumerate(layout()):
            name = f"drone_{i + 1:03d}"
            if scenario == "pad_swap" and i in (0, 1):     # file labels drones 1 and 2 the other way round
                name = f"drone_{2 - i:03d}"
            z.writestr(f"drones/{name}/trajectory.json", json.dumps(trajectory(i, scenario)))
            z.writestr(f"drones/{name}/lights.json", json.dumps({"version": 1, "data": ""}))
            show["swarm"]["drones"].append({"type": "generic", "settings": {
                "name": name, "home": [round(home[0], 4), round(home[1], 4), 0.0],
                "trajectory": {"$ref": f"./drones/{name}/trajectory.json#"},
                "lights": {"$ref": f"./drones/{name}/lights.json#"}}})
        z.writestr("show.json", json.dumps(show, indent=1))
    return path


if __name__ == "__main__":
    import sys, os
    out = sys.argv[1] if len(sys.argv) > 1 else "examples"
    os.makedirs(out, exist_ok=True)
    for sc in ["clean", "shift18", "heading7", "stale_fence", "speed_spike", "near_pass", "pad_swap"]:
        print(write_skyc(os.path.join(out, f"synthetic_{sc}.skyc"), sc))
    with open(os.path.join(out, "synthetic_venue.json"), "w") as f:
        json.dump(venue_dict(), f, indent=1)
