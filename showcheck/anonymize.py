# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 forgottenl5
"""Anonymize a .skyc so it can be shared: move the origin to a fixed fake location and strip names/metadata.

Kept (all geometry and timing): orientation, every drone's home / landAt / trajectory / lights / pyro / yaw,
geofence, validation limits, meta.segments, cue times.
Replaced: origin -> lat 0.0100, lon 0.0100 (altitude 0 if present). If the venue origin is given, the show is put at the
same East/North offset from the fake venue origin as it had from the real one, so placement errors stay visible.
Drone names -> drone_001, drone_002, ...
in file order; cue names -> "cue 1", "cue 2", ...
Dropped: title, id, sources and every other meta field, media (audio), cameras, terrain, per-drone extra/group
fields, and any ZIP member not referenced by the show. ZIP timestamps are set to a fixed date.
"""
import json
import zipfile
from .model import geo_offset, offset_latlon

FAKE_LAT, FAKE_LON = 0.0100, 0.0100
DRONE_KEEP = ("home", "landAt", "mode", "trajectory", "lights", "pyro", "yawControl")
_STAMP = (2000, 1, 1, 0, 0, 0)


def _load(z, ref):
    if isinstance(ref, dict) and "$ref" in ref:
        return json.loads(z.read(ref["$ref"].split("#")[0].lstrip("./")))
    return ref


def anonymize_skyc(src, dst, venue_origin=None):
    """Write an anonymized copy of src to dst. Returns a short list of what was changed."""
    changes = []
    with zipfile.ZipFile(src) as z:
        spec = json.loads(z.read("show.json"))
        out = {"version": spec.get("version", 1)}
        env = spec.get("environment") or {}
        new_env = {"type": env.get("type", "outdoor")}
        loc = env.get("location")
        if loc and loc.get("origin"):
            o = loc["origin"]
            lat, lon = FAKE_LAT, FAKE_LON
            if venue_origin is not None:                 # keep the show's offset from the venue origin
                de, dn, _ = geo_offset((o[0] / 1e7, o[1] / 1e7, 0.0), (venue_origin[0], venue_origin[1], 0.0))
                lat, lon = offset_latlon(FAKE_LAT, FAKE_LON, de, dn)
                changes.append(f"show kept {de:.2f} m E, {dn:.2f} m N of the (moved) venue origin")
            fake = [round(lat * 1e7), round(lon * 1e7)] + ([0] if len(o) > 2 else [])
            new_env["location"] = {"origin": fake, "orientation": loc.get("orientation", 0.0)}
            changes.append(f"origin moved to lat {lat:.7f}, lon {lon:.7f}")
        dropped = sorted(set(env) - {"type", "location"})
        out["environment"] = new_env
        meta = spec.get("meta") or {}
        if meta.get("segments"):
            out["meta"] = {"segments": meta["segments"]}
        dropped += [f"meta.{k}" for k in sorted(set(meta) - {"segments"})] + (["media"] if spec.get("media") else [])

        settings = dict(spec.get("settings") or {})
        if "cues" in settings:
            cues = _load(z, settings["cues"])
            cues = {**cues, "items": [{"name": f"cue {i + 1}", "time": c.get("time")} for i, c in enumerate(cues.get("items", []))]}
            settings["cues"] = cues
        out["settings"] = {k: v for k, v in settings.items() if k in ("cues", "geofence", "validation")}

        files, drones = {}, []
        for i, d in enumerate((spec.get("swarm") or {}).get("drones", [])):
            name = f"drone_{i + 1:03d}"
            s = d.get("settings", {})
            new = {}
            for k in DRONE_KEEP:
                if k not in s:
                    continue
                v = s[k]
                if isinstance(v, dict) and "$ref" in v:                  # rewrite referenced files under the new name
                    path = f"drones/{name}/{k}.json"
                    files[path] = _load(z, v)
                    v = {"$ref": f"./{path}#"}
                new[k] = v
            new["name"] = name
            dropped += [f"drone[{i}].{k}" for k in sorted(set(s) - set(DRONE_KEEP) - {"name"})]
            drones.append({"type": d.get("type", "generic"), "settings": new})
        out["swarm"] = {"drones": drones}
        changes.append(f"{len(drones)} drone names replaced by drone_001..")
    with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zo:
        for path, obj in [("show.json", out)] + sorted(files.items()):
            zo.writestr(zipfile.ZipInfo(path, _STAMP), json.dumps(obj, separators=(",", ":")), zipfile.ZIP_DEFLATED)
    if dropped:
        changes.append("dropped: " + ", ".join(dropped[:12]) + (" ..." if len(dropped) > 12 else ""))
    return changes


def anonymize_venue(venue):
    """Same fake origin for a venue dict (pads, crowd and fence are relative to the origin, so they stay put)."""
    v = dict(venue)
    v["origin"] = {"lat": FAKE_LAT, "lon": FAKE_LON, "alt": 0.0}
    v["name"] = "anonymized venue"
    return v
