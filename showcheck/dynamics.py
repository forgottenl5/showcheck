# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 forgottenl5
"""Speed of Bezier trajectories, and acceleration of the path as the drone's controller follows it.

Speed (.skyc): each segment is B(u), u = (t - t0)/dt in [0, 1], so velocity = B'(u)/dt.
- straight: B' constant;  quadratic: B' linear in u;  cubic: B' quadratic in u, so max |B'| is at an end or where
  d|B'|^2/du = 0 (a cubic in u).  The same works for the horizontal part alone; the vertical part is a quadratic.

Acceleration is NOT taken from the segments' second derivatives. Real exports contain many tiny segments
(a few hundredths of a second) whose B''/dt^2 is huge but meaningless: the firmware never flies a segment on its own.
It follows the show with position + velocity targets at a fixed rate (Skybrush SHOW_CTRL_RATE, 10 Hz by default,
range 1-50) with feed-forward, so it sees the path only at those instants. Here the path is sampled at that rate
and differentiated with central differences twice:
    v_k = (p_{k+1} - p_{k-1}) / 2h,    a_k = (v_{k+1} - v_{k-1}) / 2h,    h = 1 / rate.
A velocity step of dv in the file then reads as dv / 2h, not as an infinite impulse.
"""
import numpy as np

CTRL_RATE_HZ = 10.0          # Skybrush firmware SHOW_CTRL_RATE default
CTRL_RATE_RANGE = (1.0, 50.0)


def _vel_coeffs(cp):
    """B'(u) = a u^2 + b u + c (per unit u) for 2, 3 or 4 control points of any dimension."""
    n = len(cp) - 1
    z = np.zeros(cp.shape[1])
    if n == 1:
        return z, z, cp[1] - cp[0]
    if n == 2:
        p0, p1, p2 = cp
        return z, 2 * (p2 - 2 * p1 + p0), 2 * (p1 - p0)
    p0, p1, p2, p3 = cp
    q0, q1, q2 = p1 - p0, p2 - p1, p3 - p2
    return 3 * (q0 - 2 * q1 + q2), 6 * (q1 - q0), 3 * q0


def segment_max_speed(t0, t1, cp):
    """(max |velocity|, t of it) over one segment. cp: (n, k) control points incl. both ends, any k (3-D, xy, ...)."""
    dt = t1 - t0
    if dt <= 0:
        return 0.0, t0
    a, b, c = _vel_coeffs(np.asarray(cp, float))
    # |B'(u)|^2 = |a u^2 + b u + c|^2 ; stationary points: (a u^2 + b u + c) . (2 a u + b) = 0
    coeffs = [2 * a @ a, 3 * a @ b, b @ b + 2 * a @ c, b @ c]
    while coeffs and abs(coeffs[0]) < 1e-15:
        coeffs = coeffs[1:]
    cand = [0.0, 1.0]
    if len(coeffs) > 1:
        cand += [r.real for r in np.roots(coeffs) if abs(r.imag) < 1e-9 and 0 < r.real < 1]
    u = np.array(cand)
    sp = np.linalg.norm(np.outer(u * u, a) + np.outer(u, b) + c, axis=1) / dt
    i = int(np.argmax(sp))
    return float(sp[i]), t0 + u[i] * dt


def segment_z_range(t0, t1, cp):
    """(max vertical velocity (up, >= 0), t, max descent rate (>= 0), t) over one segment."""
    dt = t1 - t0
    if dt <= 0:
        return 0.0, t0, 0.0, t0
    a, b, c = (x[2] for x in _vel_coeffs(np.asarray(cp, float)))
    cand = [0.0, 1.0] + ([-b / (2 * a)] if abs(a) > 1e-15 and 0 < -b / (2 * a) < 1 else [])
    u = np.array(cand)
    vz = (a * u * u + b * u + c) / dt
    i, j = int(np.argmax(vz)), int(np.argmin(vz))
    return max(float(vz[i]), 0.0), t0 + u[i] * dt, max(float(-vz[j]), 0.0), t0 + u[j] * dt


def split_left(cp, u):
    """Control points of the part u' in [0, u] of a Bezier (de Casteljau)."""
    pts, left = np.asarray(cp, float), []
    while len(pts):
        left.append(pts[0])
        pts = (1 - u) * pts[:-1] + u * pts[1:]
    return np.array(left)


def drone_speeds(segments, t_end=None):
    """Max speed over a drone's segments up to t_end: 3-D ("v"), horizontal ("vxy"), climb ("vup") and descent
    ("vdown"), with times."""
    best = {k: 0.0 for k in ("v", "tv", "vxy", "tvxy", "vup", "tvup", "vdown", "tvdown")}
    for t0, t1, cp in segments:
        if t1 <= t0 or (t_end is not None and t0 >= t_end):
            continue
        cp = np.asarray(cp, float)
        if t_end is not None and t1 > t_end:
            cp, t1 = split_left(cp, (t_end - t0) / (t1 - t0)), t_end
        for key, (val, tt) in (("v", segment_max_speed(t0, t1, cp)), ("vxy", segment_max_speed(t0, t1, cp[:, :2]))):
            if val > best[key]:
                best[key], best["t" + key] = val, tt
        up, tu, down, td = segment_z_range(t0, t1, cp)
        if up > best["vup"]:
            best["vup"], best["tvup"] = up, tu
        if down > best["vdown"]:
            best["vdown"], best["tvdown"] = down, td
    return best


def sampled_acceleration(at, t_start, t_end, rate=CTRL_RATE_HZ, t_ref=0.0):
    """Acceleration of one drone's path sampled at `rate` Hz (central differences twice, see module docstring).

    The grid is t_ref + k / rate and runs two samples past each end, so starting from and stopping to rest counts.
    Returns (times (N,), acceleration vectors (N, 3)).
    """
    h = 1.0 / rate
    k0 = int(np.floor((t_start - t_ref) * rate)) - 2
    k1 = int(np.ceil((t_end - t_ref) * rate)) + 2
    t = t_ref + np.arange(k0, k1 + 1) * h
    p = at(t)
    v = (p[2:] - p[:-2]) / (2 * h)                    # at t[1:-1]
    a = (v[2:] - v[:-2]) / (2 * h)                    # at t[2:-2]
    return t[2:-2], a


def acceleration_peaks(at, t_start, t_end, rate=CTRL_RATE_HZ, t_ref=0.0):
    """Max |a| 3-D ("a"), horizontal ("axy") and vertical ("az") of one drone, with times ("ta", "taxy", "taz")."""
    t, a = sampled_acceleration(at, t_start, t_end, rate, t_ref)
    out = {}
    for key, mag in (("a", np.linalg.norm(a, axis=1)), ("axy", np.linalg.norm(a[:, :2], axis=1)), ("az", np.abs(a[:, 2]))):
        i = int(np.argmax(mag)) if len(mag) else 0
        out[key], out["t" + key] = (float(mag[i]), float(t[i])) if len(mag) else (0.0, t_start)
    return out
