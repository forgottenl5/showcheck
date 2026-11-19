# ShowCheck

<p align="center"><img src="demo/showcheck_demo_preview.gif" width="280" alt="ShowCheck demo (synthetic test show)"></p>

<p align="center"><em>20-second demo (synthetic test show)</em><br>
Full 20-second video with sound: <a href="demo/showcheck_demo.mp4">demo/showcheck_demo.mp4</a> (download)</p>

An independent pre-launch check of a drone-show file against the venue plan.

ShowCheck reads the show file (Skybrush `.skyc`, or `.vviz` at visualization grade) and a small venue file you write
(origin, heading, pads, crowd areas, fence, limits). It then gives a PASS / WARN / FAIL verdict per check, an HTML
report with a top-view picture, and a fingerprint manifest.

## The six checks

| Check | What it looks at |
|---|---|
| **C1 Placement** | The show's origin and heading match the venue plan, and every drone's home is on its assigned pad. |
| **C2 Crowd distance** | No drone comes closer to a crowd area than that area's buffer. |
| **C3 Geofence** | The file's fence matches the venue fence, every drone stays inside it with the buffer margin, and maximum altitude is within the limit. |
| **C4 Speed and acceleration** | Max speed and acceleration are within the venue limits. Speed comes from the derivative of the `.skyc` curves. Acceleration (method and rate are printed in the C4 line): 10 Hz sampled positions by default (`--ctrl-rate` 1-50, the drone's show control rate), central difference of central-difference velocity; values depend on the rate. This is used because the firmware follows the show at that rate and tiny segments in real exports would otherwise give meaningless spikes. Skybrush Studio estimates velocity and acceleration by backward differences at the scene frame rate (skybrush-io/studio-blender, src/modules/sbstudio/plugin/tasks/safety_check.py, commit 0a99e22), so its acceleration values can be higher than ShowCheck's 10 Hz figures on the same show. The file's own `settings.validation` levels (`maxVelocityXY/Z/ZUp`, `maxAccelerationXY/Z`) are designer warnings: they can WARN, never FAIL. |
| **C5 Spacing** | The closest approach between any two drones is at least the venue's minimum spacing (FAIL), and at least the file's `minDistance` (WARN only). Pairs where either drone is below `minNavAltitude` are skipped, as Skybrush does. The value comes from the venue, else the file, else 2.5 m (Skybrush's default takeoff altitude). The report says which was used and how many pair-samples were skipped. |
| **C6 Fingerprint** | One SHA-256 per drone (trajectory and lights) plus the placement, to compare with what each drone actually received. |

C2-C5 check each drone only up to min(end of its trajectory, its `landingTime`), because the Skybrush firmware cuts the trajectory near the end for precision landing (advice from Skybrush, 19 Nov 2026). See FORMAT_NOTES.md for the rule and why `landAt` (a position only) is not used.

## Quick start

Needs Python 3.10 or later.

```
git clone https://github.com/forgottenl5/showcheck
cd showcheck
pip install -r requirements.txt

python -m showcheck.cli examples/synthetic_clean.skyc   --venue examples/synthetic_venue.json --out reports
python -m showcheck.cli examples/synthetic_shift18.skyc --venue examples/synthetic_venue.json --out reports
```

Open `reports/synthetic_shift18.html` in a browser. It shows the verdict table with the numbers behind each
verdict, the top view, and the manifest. The exit code is 1 if any check FAILs.

Other commands:

```
python -m showcheck.cli compare A.manifest.json B.manifest.json      # which drones differ or are missing
python -m showcheck.cli anonymize my_show.skyc shareable.skyc --venue my_venue.json shareable_venue.json
python -m pytest tests                                                # run the test suite
```

The venue file format is described in `venue_format.md`, with an example in `venue_example.json`. What is known
about the file formats, and how sure we are of each point, is in `FORMAT_NOTES.md`.

## What is verified, and what is not

**Verified:** the `.skyc` axes, heading and timing conventions were confirmed against real Skybrush Studio exports.
ShowCheck's positions match Blender's own positions to within about 10 cm. They also match Skybrush's
[published `.skyc` documentation](https://docs.skybrush.io/public/skybrush-file-formats/skyc/index.html).

The geofence layout (`polygons[{isInclusion, points}]` plus `action`) now comes from Skybrush's published schema. The
units and frame of the fence points are still unconfirmed; we have asked Skybrush. Skybrush Studio exports carry no
fence, so with those files C3 checks against the venue fence only.

**Not verified:**
- Any real show or real fleet. The examples are synthetic.
- `.vviz` is visualization-grade only: no heading, no fence, and positions between samples are interpolated
  linearly.

**Not a safety certification. Use alongside your normal procedures.**

## Handling untrusted files

Show files from other people are treated as hostile until checked. ShowCheck **never extracts a `.skyc` to disk
and never executes anything in a file**. ZIP members are streamed into memory, after the ZIP headers have been checked.

What is checked before any show data is used:
- **ZIP structure**, from the headers and again while reading (a byte counter, because headers can lie): file size,
  member count, uncompressed size per member and in total, and compression ratio per member (zip bombs). It also
  rejects member names that are absolute or contain `..`, `\`, a drive letter or NUL, duplicate names, symlinks,
  encrypted members, and `$ref` links that point outside the ZIP.
- **JSON**: file size, nesting depth, and no `NaN` / `Infinity` / overflowing numbers.
- **Show content**: number of drones, segments per drone, total trajectory points, show duration, light program
  length, the memory needed for the 10 Hz sampling, and finite numbers everywhere.
- **Time**: one file may take at most 600 s (reading, checks and report), then ShowCheck stops.

Any rejected file prints one line, `ERROR: rejected: <file>: <reason>`, and exits with **code 2** (1 = a check
FAILed, 0 = all good). No Python traceback is shown; set `SHOWCHECK_DEBUG=1` if you need one.

| Limit | Default | Option |
|---|---|---|
| Input file size | 200 MB | `--max-file-mb` |
| ZIP members | 20,000 | `--max-members` |
| Uncompressed size per member | 100 MB | `--max-member-mb` |
| Uncompressed size, all members | 1024 MB | `--max-total-mb` |
| Compression ratio per member (members over 64 KiB) | 100:1 | `--max-ratio` |
| JSON nesting depth | 64 | `--max-json-depth` |
| Drones | 20,000 | `--max-drones` |
| Trajectory segments per drone | 100,000 | `--max-segments` |
| Trajectory points, all drones | 10,000,000 | `--max-points` |
| Show duration | 7200 s (2 h) | `--max-duration-s` |
| Light program per drone (bytecode bytes; `.vviz`: payload actions) | 1,000,000 | `--max-light-length` |
| Drones x 10 Hz samples held in memory | 100,000,000 | `--max-samples` |
| Processing time per file | 600 s | `--max-seconds` |

All limits are defined in one place, `showcheck/limits.py`. Every command (`check`, `compare`, `anonymize`) accepts the
options, e.g. `python -m showcheck.cli big_show.skyc --venue v.json --max-drones 30000`. Raising a limit is safe for
files you trust. Memory use grows with drones x show length, so a very large show may need a large machine.

To report a security problem, see `SECURITY.md`.

## Try it on your show

Run it locally on your own show; nothing is uploaded. Then tell us what it caught, what it missed, or what it
flagged wrongly, via [GitHub Issues](https://github.com/forgottenl5/showcheck/issues) or forgotteni5.lab@gmail.com.

Before sharing any file, anonymize it with `python -m showcheck.cli anonymize`. This moves the origin to a fake
location (lat 0.0100, lon 0.0100) and strips names and metadata, while keeping the geometry. With `--venue`, the
venue moves too and keeps its offset to the show, so a placement error is still visible.

## Licence

AGPL-3.0 (see `LICENSE`). Commercial licences are available on request; see `COMMERCIAL.md`.
ShowCheck is independent of Skybrush and contains no Skybrush code.
