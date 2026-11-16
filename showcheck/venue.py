# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 forgottenl5
"""Venue file (ShowCheck's own JSON format, see venue_format.md)."""
import numpy as np
from .limits import load_json_file

DEFAULT_TOL = {"origin_m": 2.0, "heading_deg": 2.0, "pad_m": 1.0, "fence_match_m": 1.0}


def _poly(pts, what):
    a = np.array(pts, float)
    if a.ndim != 2 or a.shape[1] != 2 or len(a) < 3:
        raise ValueError(f"{what}: need a list of at least 3 [east, north] points")
    return a


def load_venue(path_or_dict) -> dict:
    v = path_or_dict if isinstance(path_or_dict, dict) else load_json_file(path_or_dict)
    o = v["origin"]
    out = {"name": v.get("name", ""),
           "origin": (float(o["lat"]), float(o["lon"]), float(o.get("alt", 0.0))),
           "heading": float(v["heading_deg"]),
           "tol": {**DEFAULT_TOL, **v.get("tolerances", {})},
           "pads": np.array(v.get("pads", []), float).reshape(-1, 2),
           "crowd": [{"name": c.get("name", f"crowd {i + 1}"), "poly": _poly(c["polygon"], "crowd polygon"),
                      "buffer": float(c.get("buffer_m", 0.0))} for i, c in enumerate(v.get("crowd_areas", []))],
           "fence": None, "fence_buffer": 0.0, "limits": dict(v.get("limits", {})),
           "pad_assignment": {str(k): int(i) for k, i in (v.get("pad_assignment") or {}).items()}}
    bad = [k for k, i in out["pad_assignment"].items() if not 0 <= i < len(out["pads"])]
    if bad:
        raise ValueError(f"pad_assignment points outside the pads list for: {bad[:5]}")
    if v.get("geofence"):
        out["fence"] = _poly(v["geofence"]["polygon"], "geofence polygon")
        out["fence_buffer"] = float(v["geofence"].get("buffer_m", 0.0))
    return out
