# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 forgottenl5
"""C6 fingerprint: one SHA-256 per drone trajectory and light program, plus a show-level hash.

Hash method: SHA-256 of the object's canonical JSON (keys sorted, no whitespace, UTF-8, ensure_ascii=False).
That makes the hash independent of formatting/ZIP compression but sensitive to any value change.
The show hash is SHA-256 over the sorted lines "<drone id>:<trajectory hash>:<lights hash>[:<pyro hash>]".
The placement hash covers what the drones do NOT carry per drone: origin, heading, axis handedness and the file fence.
"""
import hashlib
import json
import os

METHOD = "sha256 of canonical JSON (sort_keys, separators=(',', ':'), UTF-8)"


def canon_hash(obj):
    s = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def file_hash(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def manifest(show):
    drones = []
    for d in show.drones:
        raw = d.raw or {}
        e = {"id": d.id,
             "trajectory_sha256": canon_hash(raw.get("trajectory")),
             "lights_sha256": canon_hash(raw.get("lights"))}
        if raw.get("pyro") is not None:
            e["pyro_sha256"] = canon_hash(raw["pyro"])
        drones.append(e)
    placement = {"origin": list(show.origin) if show.origin else None, "heading_deg": show.heading, "axes": show.handed,
                 "fence": None if show.fence is None else [[round(float(x), 3), round(float(y), 3)] for x, y in show.fence]}
    lines = sorted(":".join([e["id"], e["trajectory_sha256"], e["lights_sha256"]] + ([e["pyro_sha256"]] if "pyro_sha256" in e else []))
                   for e in drones)
    return {"showcheck_manifest": 1, "source": os.path.basename(show.source), "format": show.fmt,
            "file_sha256": file_hash(show.source) if os.path.isfile(show.source) else None,
            "hash_method": METHOD, "drone_count": len(drones),
            "show_sha256": hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest(),
            "placement": placement, "placement_sha256": canon_hash(placement), "drones": drones}


def compare(a, b):
    """Compare two manifests -> dict(same, differ[(id, fields)], missing_in_b[ids], missing_in_a[ids])."""
    da, db = {e["id"]: e for e in a["drones"]}, {e["id"]: e for e in b["drones"]}
    differ = []
    for i in sorted(set(da) & set(db)):
        f = [k for k in ("trajectory_sha256", "lights_sha256", "pyro_sha256") if da[i].get(k) != db[i].get(k)]
        if f:
            differ.append((i, [k.replace("_sha256", "") for k in f]))
    placement = a.get("placement_sha256") == b.get("placement_sha256")
    return {"same": a["show_sha256"] == b["show_sha256"] and not differ and set(da) == set(db) and placement,
            "placement_same": placement, "differ": differ, "missing_in_b": sorted(set(da) - set(db)), "missing_in_a": sorted(set(db) - set(da))}


def c6_result(show, expected=None):
    from .checks import Result, PASS, FAIL, WARN
    m = manifest(show)
    det = [f"show hash {m['show_sha256']}", f"placement hash {m['placement_sha256']} (origin, heading, axes, fence)",
           f"file hash {m['file_sha256']}", f"method: {METHOD}"]
    st = PASS
    missing_src = [d.id for d in show.drones if not (d.raw or {}).get("lights")]
    if missing_src:
        st = WARN
        det.append(f"{len(missing_src)} drone(s) have no light program in the file (hashed as null)")
    summ = f"{m['drone_count']} drones fingerprinted, show {m['show_sha256'][:16]}..."
    if expected is not None:
        c = compare(expected, m)
        if c["same"]:
            det.append("matches the expected manifest")
            summ += ", matches expected manifest"
        else:
            st = FAIL
            summ += (f", DIFFERS from expected: {len(c['differ'])} changed, {len(c['missing_in_b'])} missing, "
                     f"{len(c['missing_in_a'])} extra" + ("" if c["placement_same"] else ", placement changed"))
            if not c["placement_same"]:
                det.append(f"placement differs: expected {expected.get('placement')} vs file {m['placement']}")
            det += [f"changed: {i} ({', '.join(f)})" for i, f in c["differ"][:20]]
            det += [f"missing in this file: {i}" for i in c["missing_in_b"][:20]]
            det += [f"not in expected manifest: {i}" for i in c["missing_in_a"][:20]]
    r = Result("C6", "Fingerprint", st, summ, det)
    r.manifest = m
    return r
