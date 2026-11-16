# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 forgottenl5
"""Exact (analytic) speed and acceleration of Bezier trajectories.

Each segment is B(u), u = (t - t0)/dt in [0, 1], so velocity = B'(u)/dt and acceleration = B''(u)/dt^2.
- straight: B' constant, B'' = 0
- quadratic: B' linear in u (max |B'| at an end), B'' constant
- cubic: B' quadratic in u: max |B'| is at an end or where d|B'|^2/du = 0 (a cubic, solved exactly);
         B'' linear in u, so max |B''| is at an end.
Where two segments meet with different velocities the acceleration is unbounded (an impulse); those
velocity jumps are reported separately.
"""
import numpy as np


def _deriv(cp):
    """Return (velocity coefficients a2, a1, a0 for u^2, u, 1) and (acceleration at u=0, u=1), in per-unit-u terms."""
    n = len(cp) - 1
    if n == 1:
        q = cp[1] - cp[0]
        z = np.zeros(3)
        return (z, z, q), (z, z)
    if n == 2:
        p0, p1, p2 = cp
        return (np.zeros(3), 2 * (p2 - 2 * p1 + p0), 2 * (p1 - p0)), (2 * (p2 - 2 * p1 + p0),) * 2
    p0, p1, p2, p3 = cp
    q0, q1, q2 = p1 - p0, p2 - p1, p3 - p2
    return (3 * (q0 - 2 * q1 + q2), 6 * (q1 - q0), 3 * q0), (6 * (q1 - q0), 6 * (q2 - q1))


def segment_extremes(t0, t1, cp):
    """(max speed, t of max speed, max |acc|, t of max acc, v(start), v(end)) for one segment."""
    dt = t1 - t0
    (a, b, c), (acc0, acc1) = _deriv(np.asarray(cp, float))
    if dt <= 0:
        return 0.0, t0, 0.0, t0, np.zeros(3), np.zeros(3)
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
    acc = np.array([np.linalg.norm(acc0), np.linalg.norm(acc1)]) / dt**2
    j = int(np.argmax(acc))
    return float(sp[i]), t0 + u[i] * dt, float(acc[j]), t0 + j * dt, c / dt, (a + b + c) / dt


def drone_extremes(segments):
    """Max speed / acceleration over a drone's segments, plus the largest velocity jump at a segment join."""
    best = {"v": 0.0, "tv": 0.0, "a": 0.0, "ta": 0.0, "jump": 0.0, "tj": 0.0}
    prev_end = np.zeros(3)                                      # drone is at rest before the first point
    for t0, t1, cp in segments:
        v, tv, acc, ta, vs, ve = segment_extremes(t0, t1, cp)
        if t1 <= t0:
            continue
        if v > best["v"]:
            best.update(v=v, tv=tv)
        if acc > best["a"]:
            best.update(a=acc, ta=ta)
        jump = float(np.linalg.norm(vs - prev_end))
        if jump > best["jump"]:
            best.update(jump=jump, tj=t0)
        prev_end = ve
    jump = float(np.linalg.norm(prev_end))                      # and at rest after the last point
    if segments and jump > best["jump"]:
        best.update(jump=jump, tj=segments[-1][1])
    return best
