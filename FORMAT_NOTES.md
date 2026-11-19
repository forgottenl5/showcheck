# ShowCheck: drone-show file format notes

Research date: 2026-11-16. Updated 2026-11-17 against Skybrush's published `.skyc` documentation (see Sources).

How sure each statement is:
- **[DOC]**: stated in official public documentation.
- **[CODE]**: read directly from the vendor's open-source code or its test fixtures.
- **[MEASURED]**: computed from files we had access to (not redistributed).
- **[GUESS]**: my inference; not confirmed by any source.

---

## 1. Formats explained

### 1.1 VVIZ (`.vviz`): Verge Aero / Finale 3D

Plain JSON [DOC]. Verge calls it a human-readable format for **visualization only, "not flight-ready or suitable for validation"** [DOC: Verge]. Several vendors use it as an interchange format; Finale 3D imports it one-way [DOC].

**Top level**

| Key | Meaning | Source |
|---|---|---|
| `version` | `"1.0"`; "only incremented when backwards compatibility-breaking changes are made" | [DOC Verge] |
| `performanceName` | Show name | [DOC Verge] |
| `coordinateFrame` | `"ogl"`: X = right, Y = **up**, Z = forward | [DOC Verge] |
| `globalReferenceFrame` | `{lat, lon, alt}`: geographic anchor of the local origin (0,0,0). Units not stated (degrees and metres, presumably) | [DOC Verge]; units [GUESS] |
| `defaultPositionRate` | Position samples per second, used when a step has no `dt` | [DOC] |
| `defaultColorRate` | Colour samples per second; `frames` are counted in 1/rate units | [DOC] |
| `timeOffsetSecs` | Optional offset for all data | [DOC] |
| `eventTags` | Optional `[{time, tagType (EffectStart/SongStart/PyroStart…), tagID, color}]` | [DOC Verge] |
| `performances` | One entry per drone | [DOC] |

**Per drone (`performances[i]`)**
- **`id`**: integer, "nominally the name of the drone" [DOC Finale].
- **`agentDescription`**:
  - `homeX`, `homeY`, `homeZ`: start position in local XYZ [DOC]; metres [GUESS].
  - `homeH`: optional initial heading in degrees [DOC].
  - `airframe`: free-form text for third-party tools; carries no meaning in the format [DOC].
  - `agentTraversal[]`: **delta steps**. `dx`, `dy`, `dz` are the change in metres from the previous position; `dh` is a heading delta (optional); `dt` is the time since the previous step in seconds (optional; default 1/`defaultPositionRate`) [DOC].
    - Absolute position = home + running sum of the deltas [DOC].
- **`payloadDescription[]`**:
  - **Light**: `lumens`, `colorType` (RGB or RGBW), `sourceType` (Dome or spot), and `payloadActions[]` with `{r, g, b, frames?}`, where r/g/b are 0–255 linear intensity.
    - **The two docs disagree on `frames`.** Finale says it is the time delta from the previous sample and "*not* a hold-for time". Verge says it is the "number of color frames to hold the color".
    - Either way, total time = Σ(`frames` or 1)/`defaultColorRate`.
  - **Pyro**: `eventTime` (s), `vdl` (Finale effect description, e.g. "10s Gold Glittering Gerb"), `partNumber`; optional `notes`, `rail`, `pin`, `pan`, `tilt`. Angles are relative to the drone, with (0,0) = straight up; probably degrees [DOC]. Pan direction: Verge says clockwise yaw.

**Origin and heading**
- The local origin is placed at `globalReferenceFrame` [DOC Verge].
- **VVIZ has no field for the compass direction the local axes point.** Finale's docs say that for Google Map imports, North points "into the screen" by default and the user can change it in Finale [DOC Finale]. So the real-world rotation lives in the importing tool, not in the file.
- Heading 0 faces the audience; positive `dh` turns counter-clockwise seen from above [DOC Finale].
- Verge says Z is "forward"; Finale says Z is "into the screen", inverted relative to Finale's own Z axis [DOC].

**Not in VVIZ at all:** geofence, takeoff/landing segments, velocity limits, any per-drone firmware or version field. The only "version" is the file's `version: "1.0"`.


### 1.2 Skybrush compiled show (`.skyc`)

**Container** [CODE, DOC]: a ZIP file. The official test fixture `test-show.skyc` (from `skybrush-io/skybrush.js`) contains:

```
show.json
drones/drone_01/trajectory.json
drones/drone_01/lights.json
drones/drone_02/...   drones/drone_03/...
```

`show.json` points to the per-drone files with JSON `$ref` links, e.g. `{"$ref": "./drones/drone_01/trajectory.json#"}`. The JS loader resolves these from the zip (`compiled.ts`: `$ref: 'zip:show.json'`) [CODE]. Skybrush Server later splits a `.skyc` into one compact binary `.skyb` file per drone [DOC: Live glossary].

**Units and axes** [DOC: Skybrush `.skyc` docs, "Units, coordinate system and yaw"]:
- SI units throughout: time in seconds, distances in metres, angles in degrees.
- Flat-Earth, right-handed frame relative to the show origin: X = front, Y = left, Z = up.
- `environment.location.orientation` is the bearing of the X+ axis from North, measured clockwise (0 = North, 90 = East).

**`show.json` structure** (TypeScript `ShowSpecification` in `skybrush.js/packages/show-format/src/types.ts` [CODE];
field names, types and required/optional confirmed by the published JSON schema where marked [DOC]):
- `version`: must be `1`; other versions are rejected by `validation.ts`.
- `environment`:
  - `type`: `"outdoor"` or `"indoor"`.
  - `location` (optional): `{origin, orientation}`.
  - `cameras` (optional).
  - `terrain` (optional): GLB model plus transform.
- `meta`: `title`, `id`, `sources`, `timestampOffset`, and `segments: {takeoff:[t0,t1], show:[t0,t1], landing:[t0,t1]}` (start inclusive, end exclusive, in seconds).
- `media.audio`: MP3, embedded or as a separate asset, with `startTime`.
- `settings`:
  - `cues`: `{version, items:[{name, time}]}`.
  - `geofence`: `{version, polygons: [{isInclusion, points}], action}` [DOC]. `version`, `polygons`, and each polygon's `isInclusion` and `points` are required; `action` is optional [DOC]. The `action` values (report, land, return, smartLand, smartReturn, stop, shutDown) and points as `[x, y, z]` come from code only [CODE].
  - `validation` [DOC], all optional numbers in SI units:
    - `maxAccelerationXY`, `maxAccelerationZ` (m/s²);
    - `maxVelocityXY`, `maxVelocityZ`, `maxVelocityZUp` (m/s);
    - `maxAltitude` (m **above ground level**);
    - `minDistance` (m between drones);
    - `minNavAltitude` (m): below it horizontal motion is discouraged and neighbour distances are not checked.
- `swarm.drones[]`: each `{type: "generic", settings: {...}}`.

**Per-drone `settings`** [DOC]: only `trajectory` is required. Documented optional fields: `name`, `lights`, `pyro`,
`rthPlan` (`version`, `acceleration`, `landingSpeed`, `entries` required; `landingAltitude` optional), `yawControl`,
`home`, `landAt` and `extra`. A drone without `lights` is valid; ShowCheck hashes its lights as null and C6 WARNs.
- From the official fixture [CODE]: `trajectory`, `lights`, `mode: "show"`, `home: [x, y, z]`.
- From the Studio exporter [CODE]: `name`, `pyro`, `yawControl: {version, setpoints: [[t, yawDeg], …]}`.
- The server's per-drone type also lists `landAt`, `rthPlan`, `group`, `extra`, `geofence`, `coordinateSystem`, `amslReference` [CODE: `skybrush-server/.../show/specification.py`].
  - [GUESS, consistent with the code] The last three are added by Skybrush Live when it uploads the show to each drone; they are not stored per drone in the `.skyc`.
- The per-drone schema types (`SwarmSpecification`, `Trajectory`, `PyroProgram`, `RTHPlan`) come from `@skybrush/file-formats-doc`. That package is still not public (GitHub 404, not on npm), but Skybrush now publishes the `.skyc` documentation and the JSON schema built from it [DOC].

**Origin and orientation** [CODE: `studio-blender/src/modules/sbstudio/model/location.py`]:
- `environment.location.origin` = `[round(lat·1e7), round(lon·1e7), round(amsl_m·1e3)?]`. These are **integers**: latitude and longitude in 1e-7 degrees, then the optional AMSL altitude in **millimetres**. It is the *proposed* show origin [DOC: https://docs.skybrush.io/public/skybrush-file-formats/skyc/coordinates.html, Units]. `readers.py` reads exactly this (integers → ÷1e7 and ÷1e3; altitude missing → 0 m, noted).
- `orientation` = "angle of the X+ axis of the show coordinate system relative to North (towards East), in degrees". It is labelled a *proposed* placement; the operator fixes the final placement in Live. Bearing from North, clockwise, is now also [DOC].
- The coordinates page gives the encoding above [DOC: https://docs.skybrush.io/public/skybrush-file-formats/skyc/coordinates.html]. Its coordinate-system section calls the show origin's altitude "AMSL/AGL" (as set by Skybrush Live); the schema lists `origin` as three integers [DOC: https://docs.skybrush.io/public/skybrush-file-formats/skyc/schema.html]. Real Studio exports omit the altitude [confirmed by real export].
- At flight time the server receives `coordinateSystem = {origin, orientation (as a string), type: "nwu" | "neu"}` [CODE: `ext/show/metadata.py`].
  - That docstring says the origin is "(longitude, latitude)", **the opposite order from the `.skyc` origin** [CODE]. ShowCheck must not mix the two up.
  - This is the Skybrush server/Live wire format, not found in real .skyc files (per Skybrush, 19 Nov 2026). `readers.py` keeps a fallback for it, labelled as such.
- `amslReference` is the AMSL altitude that Z is measured from; if absent, Z is relative to home altitude [CODE].

**Axes** [DOC]:
- X = front, Y = left, Z = up: right-handed, Z up. This is the NWU-type frame ShowCheck assumes, and it matches the real export (see Ground-truth status).
- Z is up in the fixture too: drones climb from z = 1 to z = 15 with home z = 0 [CODE].
- The server's `coordinateSystem.type` (`nwu` / `neu`) is not stored in the `.skyc` [CODE]; ShowCheck still honours it if present.

**Trajectory** (`trajectory.json`) [CODE: `trajectory.ts`, fixture]:
- Format: `{version: 1, points: [[t, [x,y,z], [controlPoints…]], …], takeoffTime, landingTime}`. `version` and `points` are required; `takeoffTime` is optional and **defaults to 0**; `landingTime` is optional [DOC].
- **Check window (C2-C5).** Skybrush (T. Nepusz, #dev, 19 Nov 2026): the firmware cuts the trajectory near the end for precision landing, so validation should stop at the landing. ShowCheck checks each drone from the start of the show up to **min(end of trajectory, `landingTime`)**. `landingTime` is "the landing time of the drone in seconds since the start of the show" [DOC: https://docs.skybrush.io/public/skybrush-file-formats/skyc/schema.html], absolute, not shifted by `takeoffTime` [confirmed by real export]. Without `landingTime` the whole trajectory is checked. Speed and acceleration (C4) are taken over the same window. C6 still hashes the whole file, because what is uploaded is the whole trajectory.
  - `settings.landAt` is **only a position** ("the landing position of the drone", `[x, y, z]`; inferred from the last point if missing) [DOC: https://docs.skybrush.io/public/skybrush-file-formats/skyc/schema.html]. It has no time, so it does not set the window. "First time the trajectory reaches landAt" was tried and rejected: in the official hover, loiter and sync test shows the drones reach that point at the start of the hover, or pass through it on every loop. That rule cut 3600 s, 3599 s and 76 s of flight. `landingTime` cuts 0 s on all four official shows.
- Each segment runs from the previous point to this one: no control points means a straight line; 1 point means a quadratic Bézier; 2 points mean a cubic. Anything else is rejected. These are continuous Bézier curves, not sampled positions [DOC: Viewer 2.6.0 notes].
- Times are relative; the player shifts them by `takeoffTime` (`_shiftSegments(takeoffTime)`). [GUESS] So absolute time = t + takeoffTime.
  - The fixture has points from 0 to 10 s with `takeoffTime: 11.66` and `landingTime: 225.92`. That suggests the fixture was trimmed and only covers part of the show.

**Lights** (`lights.json`): `{version: 1, data: "<base64>"}`. The data is a compiled LED bytecode program, decoded by `lights.ts` and by `pyledctrl` (GPL-3.0) [CODE].

**Home, takeoff and landing:**
- `home` and `landAt` are arrays of relative coordinates; the docs refer to a separate schema for their type that is not shown [DOC].
- Per-drone `home` is used to match a physical drone to a trajectory; launch is blocked if no drone matches a home position [third-party wiki, AIMotionLab fork; not official].
- `landAt` holds the landing position [CODE: server type].
- `meta.segments` holds the takeoff/show/landing time windows [CODE].

### 1.3 Other related public formats

| Format | Status | What it is |
|---|---|---|
| `.skyb` (Skybrush binary, per drone) | Official C parser `libskybrush` (GitHub licence: NOASSERTION). Format docs now published [DOC]: blocks for trajectories, light programs, **RTH plans**, comments, yaw control, event lists, GCS light control setup and metadata, with an ImHex spec | What the drone actually flies |
| Skybrush CSV export | [DOC] Export panel | ZIP with one CSV per drone: time (ms), X, Y, Z (m), R, G, B (0–255 linear). Needs a separately distributed add-on |
| MAVSDK Drone Show (MDS) CSVs | [DOC] project docs (`alireza787b/mavsdk_drone_show`, GitHub licence NOASSERTION) | Per-drone time-stamped CSV with N/E/D + RGB for shows, and lat/lon/MSL waypoint CSVs for swarm missions. Column layout changes between versions (see their CSV migration guide) |
| Crazyswarm / Crazyswarm2 trajectories | MIT; open-source research swarm (Crazyflie) | Piecewise-polynomial trajectory CSVs; indoor research, not commercial shows. Mentioned only for completeness |
| Depence `.ddsf`, DSS PATH/PATH3, Drotek, EVSKY, HighGreat DAC, LiteBee | Named in Skybrush's export list, but **no public spec found** | Not usable without vendor documentation |

---

## 2. Fields we can check

| Check | VVIZ | .skyc |
|---|---|---|
| **Origin** | `globalReferenceFrame.lat/lon/alt` [DOC]. Check: present, not 0/0, inside the expected venue. | `environment.location.origin` (lat·1e7, lon·1e7, mm) [CODE]. Optional, and only a *proposal*. Watch the lat/lon order versus the server's `coordinateSystem`. |
| **Heading / orientation** | **No field.** Rotation is set in the importing tool [DOC Finale]. ShowCheck must ask the user for it (or assume Finale's "north into screen" default) and say so. | `environment.location.orientation`: X+ axis in degrees from North towards East [CODE]. Check: present and matches the venue plan. |
| **Home / launch positions** | `homeX/Y/Z` [DOC]. Check spacing, duplicates, a flat grid. | `settings.home` per drone [CODE]. Same checks; also `landAt`. |
| **Closest approach** | Rebuild positions (home + cumulative deltas, `dt` rule) [DOC]. Path *between* samples is unknown; I used linear interpolation [GUESS]. | Evaluate the Bézier segments at 10 Hz [CODE]. Compare against `settings.validation.minDistance` if set (warning level: WARN only). |
| **Geofence** | **No field.** Can only check against a user-supplied polygon, or report the bounding box/hull. | `settings.geofence.polygons[{isInclusion, points}]` + `action` [DOC]. Check every trajectory stays inside the inclusion polygons and outside the exclusion ones. Point dimensions, units and frame are not documented; probably show-frame metres [GUESS]. |
| **Speed** | Finite differences on the reconstructed track. | Bézier first derivative [CODE]. Compare with `maxVelocityXY`, `maxVelocityZ`, `maxVelocityZUp` (warning levels: WARN only). |
| **Acceleration** | Only rough: linear interpolation between samples creates corner spikes (artefacts). Needs smoothing or knowledge of how Verge interpolates [GUESS]. | **Not** the Béziers' second derivative: real exports have many tiny segments (one official demo show: 4410 of 4838 segments have no control points, the shortest is 0.022 s), and per-segment B''/dt² gave 371.9 m/s². The firmware follows the show with position + velocity targets at `SHOW_CTRL_RATE` (10 Hz default, 1-50) with feed-forward [CODE: firmware], and Studio estimates velocity and acceleration from positions at nearby frames [CODE: Studio]. ShowCheck therefore samples the path at the control rate and uses central differences twice. Venue `max_accel_mps2` can FAIL; `validation.maxAccelerationXY` / `maxAccelerationZ` [DOC] can only WARN. |
| **Altitude** | Max/min Y [DOC: Y up]. | Z max vs `validation.maxAltitude` (m AGL [DOC]); `amslReference` if present. |
| **Spacing / takeoff / landing** | Home spacing; path to the final position. | Closest pair vs `validation.minDistance`, skipping pairs below `validation.minNavAltitude` [DOC]. `meta.segments` windows, `takeoffTime`/`landingTime`, `home` vs `landAt` spacing. |
| **Per-drone version** | None. Only file `version: "1.0"` and free-text `airframe`. | File `version: 1`, plus `version: 1` inside each trajectory, lights, pyro, cue sheet and geofence [CODE]. **No firmware or airframe version per drone**; `type: "generic"` only. |
| **Timing consistency** | Position total vs light total per drone (should match). | Trajectory end vs `landingTime` vs `meta.segments`. |
| **Pyro** | `eventTime` within the show time, and the drone is airborne and clear of others at that moment. | `settings.pyro` (exists per the Studio exporter; schema not public) [CODE]. |

---

## 3. What is missing or unknown

1. **No real flight `.skyc` files.** Besides our own small Studio exports (see Ground-truth status), the only public `.skyc` files found are the two small official test fixtures in `skybrush-io/skybrush.js` (`test-show.skyc`, `test-show-with-assets.skyc`; 3 drones; GPL-3.0-or-later repo). Using them for testing is fine; shipping them inside a closed product would bring in the GPL [GUESS; not legal advice].
2. **The `.skyc` and `.skyb` docs are now published** by Skybrush (see Sources). The `.skyc` pages give units, axes and the JSON schema; the `.skyb` pages describe its blocks, including RTH plans. The source package `@skybrush/file-formats-doc` is still not public. What the docs leave open is listed under "Still unclear in the docs" below.
3. **VVIZ heading:** there is no field linking local axes to North. Placement in the world needs outside information.
4. **VVIZ interpolation between samples** is undocumented (linear? spline?). Closeness and acceleration between samples are estimates.
5. **The VVIZ `frames` meaning** (time delta vs hold) is contradictory between Verge and Finale.
6. **Units** of `globalReferenceFrame.alt` and `homeX/Y/Z` (VVIZ) are not stated anywhere; metres are assumed [GUESS]. `.skyc` uses SI units (s, m, deg) [DOC], but the geofence point units are not given (see below).
7. **Third-party formats** (Depence `.ddsf`, DSS, HighGreat, EVSKY…) have no public specs.
8. **Licensing for ShowCheck:**
    - All Skybrush repos are GPL-3.0, except `libskybrush` (NOASSERTION on GitHub) and `skybrush-log-analyzer-cli` (MIT).
    - [GUESS] An independent checker should be written from the documented and observed *format* (that's interoperability), not by copying GPL code, unless ShowCheck itself will be GPL.

### Still unclear in the docs

The published `.skyc` docs leave these open:
- **Origin:** the encoding (code and real export show integers: lat/lon × 10⁷, altitude in mm), the altitude unit, and whether the altitude is AMSL or AGL. The docs say it can be either and give no way to tell.
- **Geofence:** the dimensions of each point (2-D or 3-D), their frame (show-local or geodetic) and units, and the allowed `action` values. We have asked Skybrush.
- **`maxAltitude`:** documented as metres above ground level. ShowCheck compares altitude with Z above the show origin, which is the same only when the origin is at ground level.
- **`home` / `landAt`:** typed as arrays of "relative coordinates" from a separate schema that is not shown.
- **`rthPlan.entries`:** required, but the entry fields are not documented.

---

## 4. Sources

**VVIZ**
- Verge Aero, VVIZ Format: https://docs.verge.aero/drone-show-software/verge-design-studio/vviz-format
- Finale 3D, Drone show file format specification (vviz): https://finale3d.com/documentation/vviz-file-format/
- Finale 3D, Importing drone shows: https://finale3d.com/documentation/importing-drone-shows/

**Skybrush documentation**
- `.skyc` format overview: https://docs.skybrush.io/public/skybrush-file-formats/skyc/index.html
- `.skyc` units, coordinate system and yaw: https://docs.skybrush.io/public/skybrush-file-formats/skyc/coordinates.html
- `.skyc` JSON schema: https://docs.skybrush.io/public/skybrush-file-formats/skyc/schema.html
- Studio for Blender, Export panel: https://docs.skybrush.io/public/skybrush-studio-for-blender/latest/panels/safety_and_export/export.html
- Glossary (.skyc / .skyb): https://docs.skybrush.io/public/skybrush-live-doc/2.8.1/appendix/glossary.html and https://docs.skybrush.io/public/skybrush-studio-for-blender/latest/appendix/glossary.html
- Protocol SHOW messages: https://docs.skybrush.io/public/skybrush-protocol-spec/latest/messages/show.html
- Viewer 2.6.0 (Bézier trajectories in .skyc): https://skybrush.io/blog/2025-06-17-skybrush-viewer-2.6.0

**Skybrush source code** (GitHub organization https://github.com/skybrush-io; licences from the GitHub API)
- `skybrush.js` / `packages/show-format` (package licence GPL-3.0-or-later; repo licence not set):
  - Types: https://github.com/skybrush-io/skybrush.js/blob/main/packages/show-format/src/types.ts
  - Loader: https://github.com/skybrush-io/skybrush.js/blob/main/packages/show-format/src/compiled.ts
  - Trajectory: https://github.com/skybrush-io/skybrush.js/blob/main/packages/show-format/src/trajectory.ts
  - Validation: https://github.com/skybrush-io/skybrush.js/blob/main/packages/show-format/src/validation.ts
  - Fixtures: https://github.com/skybrush-io/skybrush.js/tree/main/packages/show-format/test/fixtures
- `skybrush-server` (GPL-3.0):
  - Per-drone spec: https://github.com/skybrush-io/skybrush-server/blob/main/src/flockwave/server/show/specification.py
  - Coordinate system: https://github.com/skybrush-io/skybrush-server/blob/main/src/flockwave/server/ext/show/metadata.py
- `studio-blender` (GPL-3.0):
  - Show location: https://github.com/skybrush-io/studio-blender/blob/main/src/modules/sbstudio/model/location.py
  - Exporter: https://github.com/skybrush-io/studio-blender/blob/main/src/modules/sbstudio/api/studio.py
- `live` (GPL-3.0), `viewer` (GPL-3.0), `flockwave-spec` (GPL-3.0), `pyledctrl` (GPL-3.0), `libskybrush` (NOASSERTION)

**Third-party references** (not official)
- AIMotionLab skybrush-server fork wiki (zip layout, `home` matching): https://github-wiki-see.page/m/AIMotionLab-SZTAKI/skybrush-server/wiki/Changes-from-stock-Skybrush
- Go reader (zyxkad/drone): https://pkg.go.dev/github.com/zyxkad/drone/ext/skybrush

**Other formats**
- MAVSDK Drone Show docs: https://alireza787b.github.io/mavsdk_drone_show/ (CSV migration: https://alireza787b.github.io/mavsdk_drone_show/guides/csv-migration.html)
- Crazyswarm (MIT): https://github.com/USC-ACTLab/crazyswarm; Crazyswarm2: https://github.com/IMRCLab/crazyswarm2

## Ground-truth status (ShowCheck step 2)
Real exports were made with Skybrush Studio for Blender 5.0.4 (official GitHub release, SHA-256 1877063…4449) in portable Blender 4.5.9 LTS (blender.org, SHA-256 verified), using the free community server.
- Shows: `known4`, 4 drones with homes A (10,0), B (0,10), C (0,0), D (10,10) in Blender X/Y, origin N 0.0100 / E 0.0100, orientation 30 deg; climb to 20 m by t = 8 s, move +10 m along Blender Y over 10–15 s, land by 25 s. `known4_delayed` is identical but starts every move 5 s later (takeoff at 5 s).
- The exports and Blender's own positions every 0.125 s were compared locally; they are not part of this repository. `tests/test_showcheck.py::test_real_export_known4` reruns the comparison if you make the same exports yourself (see its docstring).

Confirmed by real export:
- **Axes:** `home`, `landAt` and trajectory points are Blender X/Y/Z unchanged (right-handed, Z up). With `orientation` = bearing of X+, Y+ lies 90 deg to the LEFT of X+ (NWU-type). At 30 deg: A (local x 10) is at E 5.00 / N 8.66; B (local y 10) is at E −8.66 / N 5.00. The file does not store handedness. [confirmed by real export]
- **Heading:** `environment.location.orientation` is a plain number in degrees, measured from North toward East (30.0 written as 30.0). [confirmed by real export]
- **Origin:** `environment.location.origin` = `[round(lat·1e7), round(lon·1e7)]`, JSON integers, with NO altitude element (0.0100 deg → 100000). [confirmed by real export]
- **Time:** point times are relative to `takeoffTime`; show time = t + `takeoffTime`. In `known4_delayed`, `takeoffTime` = 5.0 and points start at 0. Reading them as t + 5 reproduces Blender to ≤ 0.095 m; reading them without the shift is off by 16.3 m. `landingTime` is absolute (30.0 = 5 + last t 25.0). Before `takeoffTime` the drone sits at `home`. [confirmed by real export]
- **Curves:** the server re-fits the sampled motion (default 4 fps) as cubic Bézier segments `[t, [x,y,z], [cp1, cp2]]` or straight segments `[t, p, []]`. Evaluating with u linear in time matches Blender to ≤ 0.067 m (`known4`) and ≤ 0.095 m (`known4_delayed`). The fit leaves velocity jumps of up to 0.30 m/s where segments meet. [confirmed by real export]
- **Geofence:** Skybrush Studio has no geofence setting, and the export has no `settings.geofence`. A fence in a `.skyc` must come from another tool (e.g. Skybrush Live). The polygon layout (`polygons[{isInclusion, points}]`) now comes from the published schema [DOC]; point units and frame are still unconfirmed. [confirmed absent in Studio exports]
- **Other fields seen:** `settings.validation` holds maxAccelerationXY/Z, maxAltitude, maxVelocityXY/Z, maxYawRate, minDistance and minNavAltitude. All but maxYawRate are in the published schema [DOC]. ShowCheck checks maxVelocityXY/Z/ZUp and maxAccelerationXY/Z (C4) and minDistance (C5) as designer warning levels (WARN, never FAIL). That these validation limits are designer warning levels was confirmed by Skybrush (19 Nov 2026, #dev). It uses minNavAltitude (C5) with a venue value winning, and 2.5 m (firmware `SHOW_TAKEOFF_ALT` default) when neither sets it. `settings.cues` points to `cues.json`. `meta.segments.takeoff` = [0, 0] when there is no storyboard. `environment.type` = "outdoor".

Reader changes made because of this:
- Origin ×1e7 is now detected by integer type, not by size. The old > 1000 test would have misread a real origin within ~0.0001 deg of the equator or prime meridian.
- A missing origin altitude is noted.
- The NWU and time notes are marked confirmed.
- A missing fence is noted, and C3 gives WARN.
