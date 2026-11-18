# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 forgottenl5
import json
import os
import sys
import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from showcheck import synth                                    # noqa: E402
from showcheck.readers import read_skyc, read_vviz             # noqa: E402
from showcheck.venue import load_venue                         # noqa: E402
from showcheck.checks import run_checks                        # noqa: E402
from showcheck.model import sample                             # noqa: E402

# Official Skybrush fixture (GPL; NOT redistributed here). Point SHOWCHECK_SKYBRUSH_FIXTURE at a local copy.
FIXTURE = os.environ.get("SHOWCHECK_SKYBRUSH_FIXTURE", "")


def verdicts(tmp_path, scenario):
    path = synth.write_skyc(str(tmp_path / f"{scenario}.skyc"), scenario)
    res, _ = run_checks(read_skyc(path), load_venue(synth.venue_dict()))
    return {r.id: r for r in res}


def test_clean_all_pass(tmp_path):
    v = verdicts(tmp_path, "clean")
    assert {k: r.status for k, r in v.items()} == {f"C{i}": "PASS" for i in range(1, 7)}, {k: r.summary for k, r in v.items()}


def test_origin_shift_18m_toward_crowd(tmp_path):
    v = verdicts(tmp_path, "shift18")
    assert v["C1"].status == "FAIL" and v["C2"].status == "FAIL"
    assert "origin 18.00 m" in v["C1"].summary
    # with pad_assignment every home is checked against its own pad (see test_pads_assigned_shift_flags_every_drone)
    assert int(v["C1"].summary.split(", ")[-1].split()[0]) >= 90


def test_heading_off_7deg(tmp_path):
    v = verdicts(tmp_path, "heading7")
    assert v["C1"].status == "FAIL" and "+7.00 deg" in v["C1"].summary


def test_stale_geofence(tmp_path):
    v = verdicts(tmp_path, "stale_fence")
    assert v["C3"].status == "FAIL"
    assert all(v[k].status == "PASS" for k in ("C1", "C2", "C4", "C5"))


def test_speed_spike(tmp_path):
    v = verdicts(tmp_path, "speed_spike")
    top = next(d for d in v["C4"].details if d.startswith("top 5 speed"))
    assert v["C4"].status == "FAIL" and top.split(";")[0].startswith("top 5 speed: drone_001")


def test_near_pass_08m(tmp_path):
    v = verdicts(tmp_path, "near_pass")
    assert v["C5"].status == "FAIL"
    d = float(v["C5"].summary.split(" m")[0])
    assert d < 0.8 and "drone_001/drone_002" in v["C5"].summary


def test_bezier_reader_exact(tmp_path):
    """Ease segment from (r,0) to (r,h) over 10 s: z(5 s) = h/2, z(2.5 s) = h*(3u^2-2u^3)."""
    show = read_skyc(synth.write_skyc(str(tmp_path / "c.skyc"), "clean"))
    d = show.drones[0]
    z = d.at(np.array([2.5, 5.0, 10.0]))[:, 2]
    assert np.allclose(z, [10 * (3 * .25**2 - 2 * .25**3), 5.0, 10.0])
    assert show.origin[0] == pytest.approx(46.5) and show.origin[2] == pytest.approx(500.0) and show.heading == 30.0


def test_vviz_reader_minimal(tmp_path):
    spec = {"version": "1.0", "coordinateFrame": "ogl", "globalReferenceFrame": {"lat": 10.0, "lon": 20.0, "alt": 0.0},
            "defaultPositionRate": 2.0, "defaultColorRate": 10.0,
            "performances": [{"id": 7, "agentDescription": {"homeX": 1.0, "homeY": 0.0, "homeZ": 2.0, "agentTraversal": [
                {"dx": 0, "dy": 5, "dz": 0, "dt": 4.0}, {"dx": 1, "dy": 0, "dz": 0}]}, "payloadDescription": []}]}
    p = tmp_path / "m.vviz"
    p.write_text(json.dumps(spec))
    s = read_vviz(str(p))
    assert s.grade == "visualization" and s.heading is None and s.fence is None
    d = s.drones[0]
    assert d.t_end == pytest.approx(4.5)                                   # 4.0 s step + one default 0.5 s step
    assert np.allclose(d.at(np.array([2.0, 4.5])), [[1, 2, 2.5], [2, 2, 5]])  # (x, y=ogl Z, z=ogl Y), linear


@pytest.mark.skipif(not os.path.isfile(FIXTURE), reason="official Skybrush test-show.skyc not available locally")
def test_official_skybrush_fixture_reads():
    s = read_skyc(FIXTURE)
    t, P = sample(s)
    print(f"\nofficial fixture: {len(s.drones)} drones, t {t[0]:.2f}-{t[-1]:.2f} s, homes {[list(d.home) for d in s.drones]}, "
          f"z {P[:, :, 2].min():.2f}-{P[:, :, 2].max():.2f} m, origin {s.origin}, heading {s.heading}, notes {s.notes}")
    assert len(s.drones) == 3 and P[:, :, 2].max() > P[:, :, 2].min()


# ---------------------------------------------------------------- step 2
from showcheck.dynamics import segment_extremes, drone_extremes   # noqa: E402
from showcheck.fingerprint import manifest, compare                # noqa: E402
from showcheck import cli                                          # noqa: E402


def test_pads_assigned_shift_flags_every_drone(tmp_path):
    v = verdicts(tmp_path, "shift18")
    assert "100 drone(s) off-pad" in v["C1"].summary          # own pad: no lucky matches any more


def test_pad_swap_caught_only_with_assignments(tmp_path):
    show = read_skyc(synth.write_skyc(str(tmp_path / "s.skyc"), "pad_swap"))
    ven = synth.venue_dict()
    r = {x.id: x for x in run_checks(show, load_venue(ven))[0]}
    assert r["C1"].status == "FAIL" and "2 drone(s) off-pad" in r["C1"].summary
    ven.pop("pad_assignment")
    r = {x.id: x for x in run_checks(show, load_venue(ven))[0]}
    assert r["C1"].status == "WARN" and "0 drone(s) off-pad" in r["C1"].summary
    assert any("no pad_assignment" in d for d in r["C1"].details)


def test_one_to_one_matching_is_not_nearest_pad():
    from showcheck.checks import c1_origin
    import types
    show = types.SimpleNamespace(origin=None, heading=None, drones=[types.SimpleNamespace(id="a"), types.SimpleNamespace(id="b")])
    venue = load_venue({"origin": {"lat": 0, "lon": 0}, "heading_deg": 0, "pads": [[0, 0], [5, 0]]})
    ctx = {"homes": np.array([[0.2, 0, 0], [0.4, 0, 0]])}    # both nearest to pad 0; one-to-one must send one to pad 1
    r = c1_origin(show, venue, ctx)
    assert r.status == "FAIL" and "1 drone(s) off-pad" in r.summary


def test_analytic_dynamics_ease_segment():
    # ease segment 28 m in 10 s: v_max = 1.5 D/T at mid, a_max = 6 D/T^2 at the ends
    cp = np.array([[0, 0, 0], [0, 0, 0], [28, 0, 0], [28, 0, 0]], float)
    v, tv, a, ta, vs, ve = segment_extremes(0, 10, cp)
    assert v == pytest.approx(4.2) and tv == pytest.approx(5.0) and a == pytest.approx(1.68)
    assert np.allclose(vs, 0) and np.allclose(ve, 0)


def test_analytic_vs_sampled_on_clean_show(tmp_path):
    v = verdicts(tmp_path, "clean")
    s = v["C4"].summary            # exact max accel is 6*20/8^2 = 1.875 (rise to 30 m); 10 Hz sampling under-reads it
    assert "max accel 1.88 m/s2 [10 Hz 1.83]" in s and "max speed 4.20 m/s" in s and "max join jump 0.00" in s


def test_velocity_jump_reported_for_linear_spike(tmp_path):
    v = verdicts(tmp_path, "speed_spike")
    j = next(d for d in v["C4"].details if d.startswith("largest velocity jump"))
    import re
    assert "1 drone(s) over: drone_001" in j and float(re.search(r"join ([\d.]+) m/s", j).group(1)) > 13


def test_manifest_and_compare(tmp_path):
    a = manifest(read_skyc(synth.write_skyc(str(tmp_path / "a.skyc"), "clean")))
    b = manifest(read_skyc(synth.write_skyc(str(tmp_path / "b.skyc"), "clean")))
    assert a["show_sha256"] == b["show_sha256"] and compare(a, b)["same"] and len(a["drones"]) == 100
    c = manifest(read_skyc(synth.write_skyc(str(tmp_path / "c.skyc"), "near_pass")))
    cmp = compare(a, c)
    assert not cmp["same"] and cmp["differ"] == [("drone_001", ["trajectory"])]
    c["drones"] = [e for e in c["drones"] if e["id"] != "drone_050"]
    cmp = compare(a, c)
    assert cmp["missing_in_b"] == ["drone_050"]


def test_cli_compare_and_expect(tmp_path, capsys):
    ven = tmp_path / "v.json"; ven.write_text(json.dumps(synth.venue_dict()))
    out = tmp_path / "rep"
    clean = synth.write_skyc(str(tmp_path / "clean.skyc"), "clean")
    spike = synth.write_skyc(str(tmp_path / "spike.skyc"), "speed_spike")
    assert cli.main([clean, "--venue", str(ven), "--out", str(out), "--name", "clean"]) == 0
    cli.main([spike, "--venue", str(ven), "--out", str(out), "--name", "spike"])
    ma, mb = str(out / "clean.manifest.json"), str(out / "spike.manifest.json")
    assert cli.main(["compare", ma, ma]) == 0
    assert cli.main(["compare", ma, mb]) == 1 and "DIFFERS  drone_001: trajectory" in capsys.readouterr().out
    # --expect: the same show passes C6, a changed one fails C6
    assert cli.main([clean, "--venue", str(ven), "--out", str(out), "--name", "clean2", "--expect", ma]) == 0
    assert cli.main([spike, "--venue", str(ven), "--out", str(out), "--name", "spike2", "--expect", ma]) == 1
    assert '"show_sha256"' in (out / "clean.manifest.json").read_text() and "C6 fingerprint manifest" in (out / "clean.html").read_text()


# ---------------------------------------------------------------- real Skybrush Studio export (step 2 A)
# Real Skybrush Studio exports are NOT part of this repository. To rerun the check, make the two shows described in
# FORMAT_NOTES.md ("Ground-truth status") and point SHOWCHECK_REAL_EXPORTS at a folder holding, for each <name>:
# <name>.skyc, <name>_design.json and <name>_blender_truth.json ({drone name: [[t, x, y, z], ...]} from Blender).
REAL = os.environ.get("SHOWCHECK_REAL_EXPORTS", "")


@pytest.mark.parametrize("name", ["known4", "known4_delayed"])
def test_real_export_known4(name):
    """Real Skybrush Studio 5.0.4 exports (Blender 4.5 LTS). <name>_design.json holds what was typed into Blender,
    <name>_blender_truth.json holds Blender's own positions every 0.125 s. Skipped when the files are absent."""
    from showcheck.model import local_to_venue
    f = os.path.join(REAL, f"{name}.skyc")
    if not os.path.isfile(f):
        pytest.skip("real Skybrush Studio export not present")
    design = json.load(open(os.path.join(REAL, f"{name}_design.json")))
    s = read_skyc(f)
    assert len(s.drones) == 4 and s.fence is None
    assert s.origin == pytest.approx((design["lat"], design["lon"], 0.0), abs=1e-9) and s.heading == pytest.approx(design["heading"])
    # homes are Blender X/Y unchanged; heading 30 deg puts local x+ at bearing 30 and local y+ at bearing -60 (NWU)
    homes = {d.id[-1]: d.home for d in s.drones}
    assert np.allclose([homes[k][:2] for k in "ABCD"], design["homes_local"])
    v = local_to_venue(np.array([homes["A"][:2], homes["B"][:2]]), s.origin, s.heading, s.handed, s.origin)
    c, sn = np.cos(np.radians(30)), np.sin(np.radians(30))
    assert np.allclose(v, [[10 * sn, 10 * c], [-10 * c, 10 * sn]], atol=0.01)
    # time convention t + takeoffTime: the reader reproduces Blender's motion to < 0.15 m at every 0.125 s
    t0 = design.get("t_takeoff", 0.0)
    truth = json.load(open(os.path.join(REAL, f"{name}_blender_truth.json")))
    for d in s.drones:
        assert d.t_start == pytest.approx(t0) and d.landing == pytest.approx(design["keys_t_dy_z"][-1][0])
        T = np.array(truth[d.id]); tt = T[:, 0]
        P = d.at(np.clip(tt, d.t_start, d.t_end)); P[tt < d.t_start] = d.home
        assert np.linalg.norm(P - T[:, 1:], axis=1).max() < 0.15, d.id
        assert d.at(np.array([design["t_climb_end"]]))[0, 2] == pytest.approx(design["climb_alt"], abs=0.15)
        assert np.allclose(d.at(np.array([d.t_end]))[0], d.home + [0, design["move_dy"], 0], atol=0.05)   # landed 10 m along y


def test_placement_hash_catches_origin_heading_fence(tmp_path):
    base = manifest(read_skyc(synth.write_skyc(str(tmp_path / "a.skyc"), "clean")))
    for sc in ("shift18", "heading7", "stale_fence"):
        m = manifest(read_skyc(synth.write_skyc(str(tmp_path / f"{sc}.skyc"), sc)))
        c = compare(base, m)
        assert m["show_sha256"] == base["show_sha256"]          # per-drone programs are identical...
        assert not c["placement_same"] and not c["same"], sc   # ...but the placement is not


def test_anonymize_keeps_verdicts(tmp_path):
    import zipfile
    for sc in ("clean", "shift18", "near_pass"):
        src = synth.write_skyc(str(tmp_path / f"{sc}.skyc"), sc)
        dst = str(tmp_path / f"{sc}_anon.skyc")
        vin, vout = tmp_path / "v.json", tmp_path / "v_anon.json"
        vin.write_text(json.dumps(synth.venue_dict()))
        assert cli.main(["anonymize", src, dst, "--venue", str(vin), str(vout)]) == 0
        a, b = read_skyc(src), read_skyc(dst)
        assert a.origin != b.origin and abs(b.origin[0] - 0.01) < 0.001 and abs(b.origin[1] - 0.01) < 0.001
        assert json.loads(vout.read_text())["origin"]["lat"] == 0.01
        spec = json.loads(zipfile.ZipFile(dst).read("show.json"))
        assert "meta" not in spec or set(spec["meta"]) <= {"segments"}
        assert "title" not in json.dumps(spec) and "ShowCheck synthetic" not in json.dumps(spec)
        ven = synth.venue_dict()
        ra = {r.id: r for r in run_checks(a, load_venue(ven))[0]}
        rb = {r.id: r for r in run_checks(b, load_venue(json.loads(vout.read_text())))[0]}
        import re
        same = lambda r: (r.status, re.sub(r"drone_\d+", "drone", r.summary))   # symmetric show: tied closest pairs may swap
        for k in ("C1", "C2", "C3", "C4", "C5"):
            assert same(ra[k]) == same(rb[k]), (sc, k)
        # C6: per-drone programs identical, only the placement (origin) differs
        assert ra["C6"].manifest["show_sha256"] == rb["C6"].manifest["show_sha256"]
        assert ra["C6"].manifest["placement"]["origin"] != rb["C6"].manifest["placement"]["origin"]
        assert ra["C6"].manifest["placement"]["heading_deg"] == rb["C6"].manifest["placement"]["heading_deg"]
        # against the ORIGINAL venue the moved origin is caught by C1
        assert {r.id: r for r in run_checks(b, load_venue(ven))[0]}["C1"].status == "FAIL"
    # without a venue the show goes exactly to the fake origin
    from showcheck.anonymize import anonymize_skyc
    anonymize_skyc(src, str(tmp_path / "plain.skyc"))
    assert read_skyc(str(tmp_path / "plain.skyc")).origin == pytest.approx((0.01, 0.01, 0.0))


# ---------------------------------------------------------------- Skybrush published .skyc schema
import zipfile                                                  # noqa: E402


def _skyc(tmp_path, drones, validation=None, name="s.skyc"):
    """drones: list of per-drone settings dicts (inline trajectory/lights). No origin: the venue origin is used."""
    spec = {"version": 1, "environment": {"type": "outdoor"}, "swarm": {"drones": [{"type": "generic", "settings": s} for s in drones]}}
    if validation is not None:
        spec["settings"] = {"validation": validation}
    path = str(tmp_path / name)
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("show.json", json.dumps(spec))
    return path


def _traj(points, **kw):
    return {"version": 1, "points": points, **kw}


def _ground_close_air_apart():
    """Two drones 0.5 m apart on the ground; they spread to 6 m apart at 1 m, then climb to 10 m together."""
    def pts(x0, x1):
        return [[0, [x0, 0, 0], []], [5, [x0, 0, 0], []], [8, [x1, 0, 1], []], [15, [x1, 0, 10], []], [20, [x1, 0, 10], []]]
    return [{"name": "a", "home": [0, 0, 0], "trajectory": _traj(pts(0.0, -3.0), takeoffTime=0)},
            {"name": "b", "home": [0.5, 0, 0], "trajectory": _traj(pts(0.5, 3.0), takeoffTime=0)}]


def test_c5_skips_pairs_below_min_nav_altitude(tmp_path):
    from showcheck.checks import c5_spacing, prepare
    ven = load_venue(synth.venue_dict())                       # min_spacing_m 2.0
    plain = read_skyc(_skyc(tmp_path, _ground_close_air_apart(), name="plain.skyc"))
    r = c5_spacing(plain, ven, prepare(plain, ven))
    assert r.status == "FAIL" and r.summary.startswith("0.50 m")       # no minNavAltitude: pads count, as before
    assert any("no minNavAltitude" in d for d in r.details)

    show = read_skyc(_skyc(tmp_path, _ground_close_air_apart(), {"minNavAltitude": 2.5}))
    assert show.limits["min_nav_altitude_m"] == 2.5
    r = c5_spacing(show, ven, prepare(show, ven))
    assert r.status == "PASS" and r.summary.startswith("6.00 m"), r.summary
    nav = next(d for d in r.details if d.startswith("minNavAltitude"))
    assert "file settings.validation.minNavAltitude" in nav and "below 2.5 m" in nav
    # every frame until both pass 2.5 m (t ~ 9.2 s) is skipped, and nothing after it
    frames = int(nav.split(" frames")[0].split(" in ")[-1].split(" of ")[0])
    assert 90 <= frames <= 95, nav

    ven["limits"]["min_nav_altitude_m"] = 0.0                  # venue value wins over the file's
    r = c5_spacing(show, ven, prepare(show, ven))
    assert r.status == "FAIL" and any("venue limits.min_nav_altitude_m (file minNavAltitude 2.5 not used" in d for d in r.details)

    ven["limits"]["min_nav_altitude_m"] = 50.0                 # nobody ever above it: nothing checked -> WARN
    r = c5_spacing(show, ven, prepare(show, ven))
    assert r.status == "WARN" and "nothing checked" in r.summary


def test_file_acceleration_limits_used_when_venue_has_none(tmp_path):
    from showcheck.checks import c4_dynamics, prepare
    show = read_skyc(_skyc(tmp_path, _ground_close_air_apart(),
                           {"maxAccelerationXY": 6.0, "maxAccelerationZ": 1.0, "minNavAltitude": 2.5}))
    assert show.limits["max_accel_xy"] == 6.0 and show.limits["max_accel_z"] == 1.0
    ven = load_venue(synth.venue_dict())                       # venue max_accel_mps2 4.0 wins
    r = c4_dynamics(show, ven, prepare(show, ven))
    assert any("venue limits.max_accel_mps2 4 m/s2 (3-D) used; file maxAccelerationXY 6 / maxAccelerationZ 1" in d
               for d in r.details), r.details
    ven["limits"].pop("max_accel_mps2")
    r = c4_dynamics(show, ven, prepare(show, ven))
    assert any("file settings.validation maxAccelerationXY 6 / maxAccelerationZ 1 m/s2 used" in d for d in r.details)
    # straight segments: the 1.29 m/s climb stops dead at 15 s, a vertical jump over the 0.1 m/s allowed (1.0 x 0.1 s)
    vj = next(d for d in r.details if d.startswith("largest vertical velocity jump"))
    assert "2 drone(s) over" in vj and r.status == "FAIL"
    assert any(d.startswith("max horizontal acceleration") for d in r.details)


def test_drone_without_lights(tmp_path):
    drones = _ground_close_air_apart()
    drones[1]["lights"] = {"version": 1, "data": ""}           # drone "a" has no lights entry at all
    show = read_skyc(_skyc(tmp_path, drones))
    assert show.drones[0].raw["lights"] is None
    r = {x.id: x for x in run_checks(show, load_venue(synth.venue_dict()))[0]}
    assert r["C6"].status == "WARN" and any("1 drone(s) have no light program" in d for d in r["C6"].details)


def test_trajectory_without_takeoff_time_is_zero(tmp_path):
    pts = [[0, [0, 0, 0], []], [10, [0, 0, 10], []]]
    show = read_skyc(_skyc(tmp_path, [{"name": "a", "trajectory": _traj(pts)},
                                      {"name": "b", "trajectory": _traj([[0, [5, 0, 0], []], [10, [5, 0, 10], []]], takeoffTime=0)}]))
    a = show.drones[0]
    assert a.takeoff == 0.0 and a.t_start == 0.0 and a.t_end == 10.0
    assert np.allclose(a.at(np.array([5.0]))[0], [0, 0, 5])     # not shifted
    assert any("1 drone(s) have no takeoffTime; treated as 0" in n for n in show.notes)
