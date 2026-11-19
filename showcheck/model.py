# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 forgottenl5
"""Internal show model and frame transforms.

Show-local frame: x, y horizontal (metres), z up (metres above the show origin altitude).
Heading = direction of the show's x+ axis, degrees from North toward East.
handed = "nwu": y is 90 deg counter-clockwise (left) of x (right-handed, z up).  Default; a GUESS for .skyc.
handed = "neu": y is 90 deg clockwise (right) of x.
Venue frame: local East/North/Up metres around the venue origin (flat-earth approximation).
"""
from dataclasses import dataclass, field
from typing import Callable, Optional
import math
import numpy as np

R_EARTH = 6371008.8
RATE_HZ = 10.0


@dataclass
class Drone:
    id: str
    home: np.ndarray                        # (3,) show-local
    at: Callable[[np.ndarray], np.ndarray]  # times (T,) -> positions (T, 3) show-local; holds first/last point outside
    t_start: float
    t_end: float
    takeoff: Optional[float] = None
    landing: Optional[float] = None
    segments: Optional[list] = None         # .skyc: [(t0, t1, control points (n, 3) incl. both ends)], show-local
    raw: Optional[dict] = None              # source objects for fingerprints: {"trajectory": ..., "lights": ..., ["pyro": ...]}
    t_check_end: Optional[float] = None     # .skyc landingTime: end of the check window; None = t_end

    @property
    def window_end(self) -> float:
        """C2-C5 check this drone only up to here: min(end of trajectory, landingTime)."""
        return self.t_end if self.t_check_end is None else min(self.t_check_end, self.t_end)


@dataclass
class Show:
    source: str
    fmt: str                                # "skyc" | "vviz"
    grade: str                              # "flight" | "visualization"
    drones: list
    origin: Optional[tuple] = None          # (lat, lon, alt_m)
    heading: Optional[float] = None         # deg
    handed: str = "nwu"
    fence: Optional[np.ndarray] = None      # (K, 2) show-local inclusion polygon
    limits: dict = field(default_factory=dict)
    notes: list = field(default_factory=list)


def sample(show: Show, rate: float = RATE_HZ):
    """All drones on one time grid: returns (t (T,), P (D, T, 3) show-local)."""
    t0 = min(d.t_start for d in show.drones)
    t1 = max(d.t_end for d in show.drones)
    t = t0 + np.arange(int(round((t1 - t0) * rate)) + 1) / rate
    return t, np.stack([d.at(t) for d in show.drones])


def axes(heading_deg: float, handed: str = "nwu"):
    h = math.radians(heading_deg)
    ux = np.array([math.sin(h), math.cos(h)])                     # x+ axis in (E, N)
    uy = np.array([-ux[1], ux[0]]) if handed == "nwu" else np.array([ux[1], -ux[0]])
    return ux, uy


def geo_offset(origin, ref):
    """(dE, dN, dU) of `origin` relative to `ref`, both (lat, lon, alt)."""
    lat, lon, alt = origin
    rlat, rlon, ralt = ref
    dn = math.radians(lat - rlat) * R_EARTH
    de = math.radians(lon - rlon) * R_EARTH * math.cos(math.radians((lat + rlat) / 2))
    return np.array([de, dn, (alt or 0.0) - (ralt or 0.0)])


def local_to_venue(xyz, origin, heading, handed, venue_origin):
    """Show-local (..., 2 or 3) -> venue ENU (..., same)."""
    xyz = np.asarray(xyz, float)
    ux, uy = axes(heading, handed)
    off = geo_offset(origin, venue_origin)
    out = np.empty_like(xyz)
    out[..., 0] = xyz[..., 0] * ux[0] + xyz[..., 1] * uy[0] + off[0]
    out[..., 1] = xyz[..., 0] * ux[1] + xyz[..., 1] * uy[1] + off[1]
    if xyz.shape[-1] == 3:
        out[..., 2] = xyz[..., 2] + off[2]
    return out


def venue_to_local(en, origin, heading, handed, venue_origin):
    """Venue ENU (..., 2) -> show-local (..., 2). Inverse of local_to_venue (horizontal only)."""
    en = np.asarray(en, float)
    ux, uy = axes(heading, handed)
    v = en - geo_offset(origin, venue_origin)[:2]
    return np.stack([v @ ux, v @ uy], -1)


def heading_diff(a: float, b: float) -> float:
    return (a - b + 180.0) % 360.0 - 180.0


def offset_latlon(lat, lon, de, dn):
    """Move (lat, lon) by de metres East, dn metres North."""
    return (lat + math.degrees(dn / R_EARTH),
            lon + math.degrees(de / (R_EARTH * math.cos(math.radians(lat)))))
