# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 forgottenl5
"""Hostile and malformed input files. Every bad file is generated here, in a temp dir, at test time."""
import json
import os
import struct
import subprocess
import sys
import warnings
import zipfile
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from showcheck import cli                                   # noqa: E402
from showcheck.limits import Limits, LimitError, SafeZip, loads_json  # noqa: E402
from showcheck.readers import read_show                            # noqa: E402

CLEAN = os.path.join(ROOT, "examples", "synthetic_clean.skyc")
VENUE = os.path.join(ROOT, "examples", "synthetic_venue.json")


# ------------------------------------------------------------------ builders
def drone(i, points=None, lights=None):
    pts = points if points is not None else [[0, [i * 3.0, 0, 0], []], [10, [i * 3.0, 0, 10], []], [20, [i * 3.0, 0, 0], []]]
    return {"type": "generic", "settings": {"name": f"d{i}", "home": [i * 3.0, 0, 0],
                                             "trajectory": {"version": 1, "points": pts, "takeoffTime": 0},
                                             "lights": lights if lights is not None else {"version": 1, "data": ""}}}


def show_json(drones):
    return json.dumps({"version": 1, "swarm": {"drones": drones}})


def write_zip(path, members, compression=zipfile.ZIP_DEFLATED):
    """members: list of (name, bytes/str) or (ZipInfo, bytes/str)."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")                 # zipfile warns about duplicate names
        with zipfile.ZipFile(path, "w", compression) as z:
            for name, data in members:
                z.writestr(name, data)
    return str(path)


def patch_names(path, mapping):
    """Rewrite member names byte-for-byte (zipfile itself refuses or normalises names like 'C:\\x' or 'a\\0b')."""
    data = open(path, "rb").read()
    for old, new in mapping.items():
        assert len(old) == len(new) and old in data
        data = data.replace(old, new)
    open(path, "wb").write(data)
    return str(path)


def patch_declared_size(path, name, size):
    """Lie about one member's uncompressed size in both the local header and the central directory."""
    data = bytearray(open(path, "rb").read())
    n = name.encode()
    for sig, off_size, off_namelen, hdr in ((b"PK\x03\x04", 22, 26, 30), (b"PK\x01\x02", 24, 28, 46)):
        i = data.find(sig)
        while i >= 0:
            ln = struct.unpack_from("<H", data, i + off_namelen)[0]
            if bytes(data[i + hdr:i + hdr + ln]) == n:
                struct.pack_into("<I", data, i + off_size, size)
            i = data.find(sig, i + 4)
    open(path, "wb").write(bytes(data))
    return str(path)


def good_skyc(tmp_path, n=3):
    return write_zip(tmp_path / "good.skyc", [("show.json", show_json([drone(i) for i in range(n)]))])


def rejected(path, limits=None, match=None):
    with pytest.raises(LimitError) as e:
        read_show(path, limits)
    if match:
        assert match in str(e.value), str(e.value)
    return str(e.value)


# ------------------------------------------------------------------ sanity
def test_minimal_builder_is_accepted(tmp_path):
    assert len(read_show(good_skyc(tmp_path)).drones) == 3


# ------------------------------------------------------------------ ZIP structure
def test_zip_bomb_high_ratio(tmp_path):
    p = write_zip(tmp_path / "bomb.skyc", [("show.json", show_json([drone(0)])), ("pad.bin", b"\0" * (20 * 1024 * 1024))])
    rejected(p, match="possible zip bomb")


def test_too_many_members(tmp_path):
    p = write_zip(tmp_path / "many.skyc", [("show.json", show_json([drone(0)]))] + [(f"m{i}", "x") for i in range(20_000)])
    rejected(p, match="20001 members")


@pytest.mark.parametrize("bad, why", [(b"../x", "contains '..'"), (b"/etc/x", "absolute path"),
                                      (b"C:\\x", "backslash"), (b"C:/xx", "drive letter"),
                                      (b"a/../../b", "contains '..'"), (b"ab\x00c", "NUL")])
def test_path_traversal_names(tmp_path, bad, why):
    placeholder = b"Q" * len(bad)
    p = write_zip(tmp_path / "trav.skyc", [("show.json", show_json([drone(0)])), (placeholder.decode(), "x")])
    patch_names(p, {placeholder: bad})
    rejected(p, match=why)


def test_duplicate_names(tmp_path):
    p = write_zip(tmp_path / "dup.skyc", [("show.json", show_json([drone(0)])), ("show.json", show_json([drone(1)]))])
    rejected(p, match="two members named 'show.json'")


def test_symlink_entry(tmp_path):
    zi = zipfile.ZipInfo("link")
    zi.external_attr = (0o120777 << 16)
    p = write_zip(tmp_path / "link.skyc", [("show.json", show_json([drone(0)])), (zi, "/etc/passwd")])
    rejected(p, match="symbolic link")


def test_oversized_member_declared_small(tmp_path):
    big = json.dumps({"version": 1, "points": [[i, [0, 0, i % 50], []] for i in range(100_000)]})
    p = write_zip(tmp_path / "liar.skyc", [("show.json", show_json([drone(0)])), ("t.json", big)])
    patch_declared_size(p, "t.json", 1000)
    with SafeZip(p) as z:                           # headers look harmless ...
        assert z.members["t.json"].file_size == 1000
        with pytest.raises(LimitError, match="t.json"):  # ... but reading it is rejected
            z.read("t.json")


def test_member_over_size_limit_from_header(tmp_path):
    p = write_zip(tmp_path / "big.skyc", [("show.json", show_json([drone(0)])), ("pad.bin", os.urandom(300_000))])
    rejected(p, Limits(max_member_mb=0.25), match="over the 0.25 MB limit")


def test_total_uncompressed_limit(tmp_path):
    p = write_zip(tmp_path / "tot.skyc", [("show.json", show_json([drone(0)]))] +
                  [(f"p{i}.bin", os.urandom(200_000)) for i in range(4)])
    rejected(p, Limits(max_total_mb=0.5), match="total")


def test_input_file_size_limit(tmp_path):
    rejected(good_skyc(tmp_path), Limits(max_file_mb=0.0001), match="over the 0.0001 MB limit")


def test_not_a_zip(tmp_path):
    p = tmp_path / "junk.skyc"
    p.write_bytes(b"this is not a zip" * 10)
    rejected(str(p), match="not a valid ZIP")


def test_ref_outside_zip_rejected(tmp_path):
    d = drone(0)
    d["settings"]["trajectory"] = {"$ref": "./../../etc/passwd#"}
    p = write_zip(tmp_path / "ref.skyc", [("show.json", show_json([d]))])
    rejected(p, match="contains '..'")


def test_shared_ref_is_read_once(tmp_path):
    t = {"version": 1, "points": [[0, [0, 0, 0], []], [10, [0, 0, 10], []]]}
    ds = [drone(i) for i in range(50)]
    for d in ds:
        d["settings"]["trajectory"] = {"$ref": "./shared.json#"}
    p = write_zip(tmp_path / "shared.skyc", [("show.json", show_json(ds)), ("shared.json", json.dumps(t))])
    assert len(read_show(p).drones) == 50


# ------------------------------------------------------------------ JSON
def test_deeply_nested_json_skyc(tmp_path):
    deep = "[" * 100_000 + "]" * 100_000            # past Python's recursion limit
    p = write_zip(tmp_path / "deep.skyc", [("show.json", '{"version": 1, "x": ' + deep + "}")],
                  zipfile.ZIP_STORED)               # stored, so the ratio guard does not catch it first
    rejected(p, match="nested deeper than 64")


def test_moderately_nested_json_vviz(tmp_path):
    p = tmp_path / "deep.vviz"
    p.write_text('{"performances": [], "x": ' + "[" * 70 + "]" * 70 + "}")
    rejected(str(p), match="nested deeper than 64")


@pytest.mark.parametrize("tok", ["NaN", "Infinity", "-Infinity", "1e400"])
def test_non_finite_json_numbers(tok):
    with pytest.raises(LimitError):
        loads_json('{"a": [1, ' + tok + "]}")


def test_nan_coordinates_skyc(tmp_path):
    text = show_json([drone(0)]).replace("[0, [0.0, 0, 10], []]", "[0, [0.0, 0, NaN], []]")
    text = text.replace("[10, [0.0, 0, 10], []]", "[10, [0.0, NaN, 10], []]")
    assert "NaN" in text
    rejected(write_zip(tmp_path / "nan.skyc", [("show.json", text)]), match="NaN is not allowed")


def test_nan_coordinates_as_strings(tmp_path):
    d = drone(0, points=[[0, [0, 0, 0], []], [10, ["nan", 0, 10], []]])
    rejected(write_zip(tmp_path / "nans.skyc", [("show.json", show_json([d]))]), match="non-finite")


def test_nan_vviz(tmp_path):
    p = tmp_path / "nan.vviz"
    p.write_text('{"performances": [{"id": "a", "agentDescription": {"agentTraversal": [{"dx": NaN}]}}]}')
    rejected(str(p), match="NaN is not allowed")


def test_vviz_zero_rate(tmp_path):
    p = tmp_path / "rate.vviz"
    p.write_text(json.dumps({"defaultPositionRate": 0, "performances": [
        {"id": "a", "agentDescription": {"agentTraversal": [{"dx": 1}]}}]}))
    rejected(str(p), match="defaultPositionRate must be positive")


# ------------------------------------------------------------------ show content
def test_huge_drone_count(tmp_path):
    ref = {"version": 1, "points": [[0, [0, 0, 0], []], [10, [0, 0, 10], []]]}
    ds = [{"settings": {"name": f"d{i}", "trajectory": {"$ref": "./t.json#"}}} for i in range(20_001)]
    p = write_zip(tmp_path / "swarm.skyc", [("show.json", show_json(ds)), ("t.json", json.dumps(ref))])
    rejected(p, match="drones in the show: 20001")


def test_too_many_segments(tmp_path):
    pts = [[i * 0.01, [0, 0, 1], []] for i in range(1001)]
    p = write_zip(tmp_path / "segs.skyc", [("show.json", show_json([drone(0, pts)]))])
    assert len(read_show(p, Limits(max_segments=1000)).drones) == 1          # exactly at the limit is fine
    rejected(p, Limits(max_segments=999), match="trajectory segments: 1000")


def test_too_many_total_points(tmp_path):
    rejected(good_skyc(tmp_path, 5), Limits(max_points=14), match="trajectory points in the show: 15")


def test_show_too_long(tmp_path):
    d = drone(0, points=[[0, [0, 0, 0], []], [7201, [0, 0, 10], []]])
    rejected(write_zip(tmp_path / "long.skyc", [("show.json", show_json([d]))]), match="over the 7200 s limit")


def test_huge_time_values(tmp_path):
    d = drone(0, points=[[0, [0, 0, 0], []], [10 ** 400, [0, 0, 10], []]])
    rejected(write_zip(tmp_path / "huge.skyc", [("show.json", show_json([d]))]))


def test_sample_memory_limit(tmp_path):
    rejected(good_skyc(tmp_path, 10), Limits(max_samples=1000), match="samples")


def test_light_program_too_long(tmp_path):
    d = drone(0, lights={"version": 1, "data": "A" * 4000})
    rejected(write_zip(tmp_path / "lights.skyc", [("show.json", show_json([d]))]), Limits(max_light_length=2000),
             match="light program bytes: 3000")


def test_malformed_structure_is_clean_error(tmp_path):
    rejected(write_zip(tmp_path / "nos.skyc", [("show.json", '{"version": 1}')]), match="malformed show file")
    rejected(write_zip(tmp_path / "noshow.skyc", [("other.json", "{}")]), match="no member 'show.json'")


# ------------------------------------------------------------------ CLI: exit code 2, message, no traceback
def bad_files(tmp_path):
    out = {"bomb": write_zip(tmp_path / "bomb.skyc", [("show.json", show_json([drone(0)])), ("z", b"\0" * (8 << 20))]),
           "dup": write_zip(tmp_path / "dup.skyc", [("show.json", "{}"), ("show.json", "{}")]),
           "deep": write_zip(tmp_path / "deep.skyc", [("show.json", "[" * 50_000 + "]" * 50_000)]),
           "nan": write_zip(tmp_path / "nan.skyc", [("show.json", '{"version": 1, "x": NaN}')]),
           "struct": write_zip(tmp_path / "struct.skyc", [("show.json", '{"version": 1, "swarm": 5}')])}
    trav = write_zip(tmp_path / "trav.skyc", [("show.json", "{}"), ("QQQQ", "x")])
    out["trav"] = patch_names(trav, {b"QQQQ": b"../x"})
    junk = tmp_path / "junk.vviz"
    junk.write_bytes(b"\xff\xfe not json")
    out["junk"] = str(junk)
    return out


def test_cli_rejections_exit_2_without_traceback(tmp_path, capsys):
    for name, path in bad_files(tmp_path).items():
        rc = cli.main([path, "--venue", VENUE, "--out", str(tmp_path / "out")])
        err = capsys.readouterr().err
        assert rc == 2, name
        assert err.startswith("ERROR: rejected:") and "Traceback" not in err, (name, err)


def test_cli_bad_venue_and_manifest(tmp_path, capsys):
    v = tmp_path / "v.json"
    v.write_text('{"origin": {"lat": NaN, "lon": 0}, "heading_deg": 0}')
    assert cli.main([CLEAN, "--venue", str(v), "--out", str(tmp_path)]) == 2
    assert "NaN is not allowed" in capsys.readouterr().err
    m = tmp_path / "m.json"
    m.write_text("[1, 2]")
    assert cli.main(["compare", str(m), str(m)]) == 2
    assert "not a ShowCheck manifest" in capsys.readouterr().err


def test_cli_max_flag_overrides_and_resets(tmp_path, capsys):
    assert cli.main([CLEAN, "--venue", VENUE, "--out", str(tmp_path), "--max-drones", "5"]) == 2
    assert "--max-drones" in capsys.readouterr().err
    assert cli.main([CLEAN, "--venue", VENUE, "--out", str(tmp_path)]) == 0     # the override did not stick


def test_cli_anonymize_rejects_bomb(tmp_path, capsys):
    src = bad_files(tmp_path)["bomb"]
    dst = tmp_path / "anon.skyc"
    assert cli.main(["anonymize", src, str(dst)]) == 2
    assert not dst.exists() and "zip bomb" in capsys.readouterr().err


def test_time_guard_subprocess(tmp_path):
    r = subprocess.run([sys.executable, "-m", "showcheck.cli", CLEAN, "--venue", VENUE, "--out", str(tmp_path),
                        "--max-seconds", "0.001"], cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert r.returncode == 2 and "processing took longer than" in r.stderr and "Traceback" not in r.stderr


def test_subprocess_hostile_file_no_traceback(tmp_path):
    p = bad_files(tmp_path)["deep"]
    r = subprocess.run([sys.executable, "-m", "showcheck.cli", p, "--venue", VENUE, "--out", str(tmp_path)],
                       cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert r.returncode == 2 and r.stderr.startswith("ERROR: rejected:") and "Traceback" not in r.stderr


def test_examples_still_read_with_default_limits():
    names = [f for f in sorted(os.listdir(os.path.join(ROOT, "examples"))) if f.endswith(".skyc")]
    assert len(names) == 7
    for f in names:
        assert len(read_show(os.path.join(ROOT, "examples", f)).drones) > 0
