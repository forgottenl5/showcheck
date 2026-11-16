# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 forgottenl5
"""Limits for untrusted show files, and the safe ZIP / JSON readers that enforce them.

Every limit lives in `Limits` below; the CLI exposes each one as --max-... (e.g. --max-drones 30000).
Show files are never extracted to disk and nothing in them is executed: ZIP members are streamed into memory
with a byte counter, after their names and sizes have been checked from the ZIP headers.
A rejected file raises `LimitError` (a ValueError); the CLI prints it and exits with code 2.
"""
import json
import math
import os
import re
import stat
import sys
import threading
import zipfile
from dataclasses import dataclass, fields

MB = 1024 * 1024


class LimitError(ValueError):
    """The file is malformed or exceeds a safety limit."""


@dataclass
class Limits:
    max_file_mb: float = 200            # input file size on disk (.skyc, .vviz, venue, manifest)
    max_members: int = 20_000           # ZIP members
    max_member_mb: float = 100          # uncompressed size of one ZIP member
    max_total_mb: float = 1024          # uncompressed size of all ZIP members together
    max_ratio: float = 100              # uncompressed : compressed, per ZIP member (members over 64 KiB)
    max_json_depth: int = 64            # JSON nesting depth
    max_drones: int = 20_000
    max_segments: int = 100_000         # trajectory segments (points - 1) per drone
    max_points: int = 10_000_000        # trajectory points over all drones
    max_duration_s: float = 7200        # show span, first point of any drone to last point of any drone
    max_light_length: int = 1_000_000   # light program: decoded bytecode bytes (.skyc) / payload actions (.vviz)
    max_samples: int = 100_000_000      # drones x 10 Hz samples held in memory by the checks
    max_seconds: float = 600            # wall-clock time for one file, read + checks + report (CLI only)


LIMITS = Limits()                       # the active limits; the CLI overrides fields from --max-... flags

RATIO_FLOOR = 64 * 1024                 # small members cannot be bombs; the ratio test starts above this size
_ALLOWED_METHODS = {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED, zipfile.ZIP_BZIP2, zipfile.ZIP_LZMA}
_DRIVE = re.compile(r"^[A-Za-z]:")


def flag(name):
    return "--" + name.replace("_", "-")


def add_arguments(ap):
    """Add one --max-... option per limit to an argparse parser."""
    g = ap.add_argument_group("safety limits for untrusted files")
    for f in fields(Limits):
        g.add_argument(flag(f.name), type=f.type if callable(f.type) else type(f.default), default=None,
                       metavar="N", help=f"default {f.default:,}")


def apply_arguments(ns):
    """Set LIMITS from parsed --max-... options (only those given)."""
    for f in fields(Limits):
        v = getattr(ns, f.name, None)
        if v is not None:
            if v <= 0:
                raise LimitError(f"{flag(f.name)} must be positive")
            setattr(LIMITS, f.name, v)


def _lim(limits):
    return LIMITS if limits is None else limits


def check_file_size(path, limits=None):
    lim = _lim(limits)
    size = os.path.getsize(path)
    if size > lim.max_file_mb * MB:
        raise LimitError(f"file is {size / MB:.1f} MB, over the {lim.max_file_mb:g} MB limit ({flag('max_file_mb')})")
    return size


# ------------------------------------------------------------------ JSON
def _finite_float(s):
    v = float(s)
    if not math.isfinite(v):
        raise LimitError(f"number {s[:40]!r} is out of range (not finite)")
    return v


def _reject_constant(s):
    raise LimitError(f"{s} is not allowed in show files (numbers must be finite)")


def _depth_ok(obj, max_depth):
    stack = [(obj, 1)]
    while stack:
        o, d = stack.pop()
        if isinstance(o, (dict, list)):
            if d > max_depth:
                return False
            stack.extend((v, d + 1) for v in (o.values() if isinstance(o, dict) else o))
    return True


def loads_json(data, what="JSON", limits=None):
    """json.loads with: no NaN/Infinity, no overflowing floats, bounded nesting, clean errors."""
    lim = _lim(limits)
    try:
        obj = json.loads(data, parse_constant=_reject_constant, parse_float=_finite_float)
    except LimitError as e:
        raise LimitError(f"{what}: {e}") from None
    except RecursionError:
        raise LimitError(f"{what}: JSON nested deeper than {lim.max_json_depth} levels ({flag('max_json_depth')})") from None
    except (ValueError, UnicodeDecodeError) as e:
        raise LimitError(f"{what}: not valid JSON ({str(e)[:200]})") from None
    if not _depth_ok(obj, lim.max_json_depth):
        raise LimitError(f"{what}: JSON nested deeper than {lim.max_json_depth} levels ({flag('max_json_depth')})")
    return obj


def load_json_file(path, limits=None):
    """Read a JSON file from disk (size-checked, UTF-8 with or without BOM)."""
    check_file_size(path, limits)
    with open(path, "rb") as f:
        data = f.read()
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise LimitError(f"{os.path.basename(str(path))}: not UTF-8 text") from None
    return loads_json(text, os.path.basename(str(path)), limits)


# ------------------------------------------------------------------ ZIP
def check_member_name(name):
    """Reject names that could mean a different file elsewhere, or are ambiguous."""
    if "\x00" in name:
        return "contains a NUL byte"
    if "\\" in name:
        return "contains a backslash"
    if name.startswith("/"):
        return "is an absolute path"
    if _DRIVE.match(name):
        return "starts with a drive letter"
    if ".." in name.split("/"):
        return "contains '..'"
    return None


class SafeZip:
    """Read-only view of a ZIP that checks every header first and streams members with a byte counter.

    Members are only ever read into memory, never extracted. Parsed JSON members are cached, so a member
    referenced by many drones is decompressed once.
    """

    def __init__(self, path, limits=None):
        self.lim = lim = _lim(limits)
        check_file_size(path, lim)
        try:
            self.z = zipfile.ZipFile(path)
        except (zipfile.BadZipFile, zipfile.LargeZipFile, OSError, ValueError) as e:
            raise LimitError(f"not a valid ZIP file ({e})") from None
        try:
            self._check_headers()
        except BaseException:
            self.z.close()
            raise
        self.read_total = 0
        self._json = {}

    def _check_headers(self):
        lim, infos = self.lim, self.z.infolist()
        if len(infos) > lim.max_members:
            raise LimitError(f"ZIP has {len(infos)} members, over the {lim.max_members} limit ({flag('max_members')})")
        seen, total = set(), 0
        self.members = {}
        for i in infos:
            raw = i.orig_filename                      # zipfile rewrites '\\' and cuts at NUL; check what is really there
            bad = check_member_name(raw)
            if bad:
                raise LimitError(f"ZIP member name {raw!r} {bad}; rejected")
            if raw in seen:
                raise LimitError(f"ZIP has two members named {raw!r}; rejected (ambiguous)")
            seen.add(raw)
            if stat.S_ISLNK(i.external_attr >> 16):
                raise LimitError(f"ZIP member {raw!r} is a symbolic link; rejected")
            if i.flag_bits & 0x1:
                raise LimitError(f"ZIP member {raw!r} is encrypted; rejected")
            if i.compress_type not in _ALLOWED_METHODS:
                raise LimitError(f"ZIP member {raw!r} uses unsupported compression method {i.compress_type}")
            if i.file_size > lim.max_member_mb * MB:
                raise LimitError(f"ZIP member {raw!r} is {i.file_size / MB:.1f} MB uncompressed, over the "
                                 f"{lim.max_member_mb:g} MB limit ({flag('max_member_mb')})")
            if i.file_size > RATIO_FLOOR and i.file_size > lim.max_ratio * max(i.compress_size, 1):
                raise LimitError(f"ZIP member {raw!r} compresses {i.file_size / max(i.compress_size, 1):.0f}:1, over the "
                                 f"{lim.max_ratio:g}:1 limit ({flag('max_ratio')}); possible zip bomb")
            total += i.file_size
            if not raw.endswith("/"):
                self.members[raw] = i
        if total > lim.max_total_mb * MB:
            raise LimitError(f"ZIP members total {total / MB:.1f} MB uncompressed, over the {lim.max_total_mb:g} MB "
                             f"limit ({flag('max_total_mb')})")

    def read(self, name):
        """Stream one member into memory, enforcing the limits on the bytes actually produced."""
        lim = self.lim
        info = self.members.get(name)
        if info is None:
            raise LimitError(f"ZIP has no member {name!r}")
        cap = min(info.file_size, lim.max_member_mb * MB)
        ratio_cap = max(RATIO_FLOOR, lim.max_ratio * max(info.compress_size, 1))
        buf, n = bytearray(), 0
        try:
            with self.z.open(info) as f:
                while True:
                    chunk = f.read(1 << 16)
                    if not chunk:
                        break
                    n += len(chunk)
                    self.read_total += len(chunk)
                    if n > cap:
                        raise LimitError(f"ZIP member {name!r} produced more data than its header declares "
                                         f"({info.file_size} bytes) or than the member limit; rejected")
                    if n > ratio_cap:
                        raise LimitError(f"ZIP member {name!r} expands past {lim.max_ratio:g}:1 while reading; "
                                         f"possible zip bomb ({flag('max_ratio')})")
                    if self.read_total > lim.max_total_mb * MB:
                        raise LimitError(f"ZIP members expand past {lim.max_total_mb:g} MB in total ({flag('max_total_mb')})")
                    buf += chunk
        except LimitError:
            raise
        except (zipfile.BadZipFile, EOFError, OSError, RuntimeError, ValueError, NotImplementedError) as e:
            raise LimitError(f"ZIP member {name!r} is corrupt ({str(e)[:200]})") from None
        if n != info.file_size:
            raise LimitError(f"ZIP member {name!r} is {n} bytes, but its header declares {info.file_size}; rejected")
        return bytes(buf)

    def json(self, name):
        if name not in self._json:
            data = self.read(name)
            try:
                text = data.decode("utf-8-sig")
            except UnicodeDecodeError:
                raise LimitError(f"ZIP member {name!r} is not UTF-8 text") from None
            self._json[name] = loads_json(text, f"ZIP member {name!r}", self.lim)
        return self._json[name]

    def ref(self, ref):
        """Member name for a '$ref' such as './drones/x/trajectory.json#'. Only names inside this ZIP are allowed."""
        if not isinstance(ref, str):
            raise LimitError(f"$ref must be a string, got {type(ref).__name__}")
        name = ref.split("#")[0]
        while name.startswith("./"):
            name = name[2:]
        bad = check_member_name(name)
        if bad:
            raise LimitError(f"$ref {ref!r} {bad}; rejected")
        return name

    def close(self):
        self.z.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


# ------------------------------------------------------------------ show content
def need(cond, msg):
    if not cond:
        raise LimitError(msg)


def check_count(n, limit_name, what, limits=None):
    lim = _lim(limits)
    limit = getattr(lim, limit_name)
    if n > limit:
        raise LimitError(f"{what}: {n}, over the {limit:g} limit ({flag(limit_name)})")


def finite(x, what):
    """float(x), rejecting non-numbers and non-finite values with a clear message."""
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        raise LimitError(f"{what}: expected a number, got {type(x).__name__}")
    try:
        v = float(x)
    except OverflowError:
        raise LimitError(f"{what}: number out of range") from None
    if not math.isfinite(v):
        raise LimitError(f"{what}: number is not finite")
    return v


def check_show(show, limits=None):
    """Limits that need the whole show: drone count, time span, memory for the 10 Hz sampling."""
    from .model import RATE_HZ
    import numpy as np
    lim = _lim(limits)
    need(len(show.drones) > 0, "show has no drones")
    check_count(len(show.drones), "max_drones", "drones in the show", lim)
    for d in show.drones:
        need(math.isfinite(d.t_start) and math.isfinite(d.t_end), f"drone {d.id}: start/end time is not finite")
        need(bool(np.all(np.isfinite(d.home))), f"drone {d.id}: home position is not finite")
    span = max(d.t_end for d in show.drones) - min(d.t_start for d in show.drones)
    if span > lim.max_duration_s:
        raise LimitError(f"show spans {span:.0f} s, over the {lim.max_duration_s:g} s limit ({flag('max_duration_s')})")
    samples = len(show.drones) * (int(round(span * RATE_HZ)) + 1)
    if samples > lim.max_samples:
        raise LimitError(f"{len(show.drones)} drones x {span:.0f} s at {RATE_HZ:g} Hz = {samples:,} samples, over the "
                         f"{lim.max_samples:,} limit ({flag('max_samples')})")
    if show.fence is not None:
        need(bool(np.all(np.isfinite(show.fence))), "geofence has non-finite coordinates")
    if show.origin is not None:
        need(all(math.isfinite(v) for v in show.origin), "show origin is not finite")
    if show.heading is not None:
        need(math.isfinite(show.heading), "show heading is not finite")


# ------------------------------------------------------------------ time guard
def start_watchdog(seconds, label):
    """Abort the whole process (exit 2) if one file takes longer than `seconds`. Returns the timer; cancel() it."""
    def fire():
        try:
            sys.stdout.flush()
            sys.stderr.write(f"\nERROR: {label}: processing took longer than {seconds:g} s "
                             f"({flag('max_seconds')}); aborted.\n")
            sys.stderr.flush()
        finally:
            os._exit(2)
    t = threading.Timer(seconds, fire)
    t.daemon = True
    t.start()
    return t
