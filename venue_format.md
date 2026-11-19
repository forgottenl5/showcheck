# ShowCheck venue file (`*.json`)

The venue file is ShowCheck's own format. It describes the real site, independently of the show file. Every check compares the show file against it.

- **Coordinates:** all positions are **venue East/North in metres**, measured from the venue origin (flat-earth; fine for show-sized areas of a few km).
- **Altitudes:** metres above the venue origin altitude.
- **Example:** `venue_example.json`, the venue matching the synthetic test show.

```json
{
  "name": "Synthetic test venue (not a real site)",
  "origin": {"lat": 46.5, "lon": 7.5, "alt": 500.0},
  "heading_deg": 30.0,
  "tolerances": {"origin_m": 2.0, "heading_deg": 2.0, "pad_m": 1.0, "fence_match_m": 1.0},
  "pads": [[6.0, 10.392], [5.462, 10.693], "..."],
  "crowd_areas": [
    {"name": "south crowd", "polygon": [[-60, -160], [60, -160], [60, -100], [-60, -100]], "buffer_m": 50.0}
  ],
  "geofence": {"polygon": [[-80, -80], [80, -80], [80, 80], [-80, 80]], "buffer_m": 5.0},
  "limits": {"max_speed_mps": 8.0, "max_accel_mps2": 4.0, "min_spacing_m": 2.0, "max_altitude_m": 120.0}
}
```

| Field | Required | Meaning | Used by |
|---|---|---|---|
| `name` | no | Free text shown in the report | - |
| `origin.lat`, `origin.lon` | yes | Surveyed show origin, WGS-84 degrees | C1; all frame transforms |
| `origin.alt` | no (0) | Altitude of the origin in metres (AMSL or any consistent datum) | C1 (reported) |
| `heading_deg` | yes | Intended direction of the show's **x+ axis**, in degrees from North toward East (the same convention as Skybrush `environment.location.orientation`). For `.vviz` files, which carry no heading, this value is **used**, not checked. | C1; `.vviz` transform |
| `tolerances.origin_m` | no (2.0) | Allowed horizontal origin difference | C1 |
| `tolerances.heading_deg` | no (2.0) | Allowed heading difference | C1 |
| `tolerances.pad_m` | no (1.0) | Each drone's home must be within this distance of some pad | C1 |
| `tolerances.fence_match_m` | no (1.0) | Allowed max vertex difference between the file fence and the venue fence | C3 |
| `pads` | no | Launch pads `[east, north]`. If empty, the home check is skipped (WARN). | C1 |
| `pad_assignment` | no | `{"<drone id>": <index into pads>}`. C1 checks each drone against ITS pad. Without it, drones are matched to pads one-to-one (minimum total distance, not nearest pad) and C1 is at least WARN, because a swapped pair cannot be detected that way. | C1 |
| `crowd_areas[]` | no | `polygon`: at least 3 `[east, north]` points; `buffer_m`: minimum horizontal distance from any drone to the polygon at any time | C2 |
| `geofence.polygon` | no | Inclusion polygon `[east, north]` | C3 |
| `geofence.buffer_m` | no (0) | Drones must stay at least this far inside the fence | C3 |
| `limits.max_speed_mps` | no | 3-D speed limit | C4 |
| `limits.max_accel_mps2` | no | 3-D acceleration limit, checked on the path sampled at the show control rate (`--ctrl-rate`, default 10 Hz) with central differences. A `.skyc` file's own `maxAccelerationXY` / `maxAccelerationZ` (and `maxVelocityXY` / `Z` / `ZUp`) are always checked too, but only as designer warning levels: they can WARN, never FAIL. | C4 |
| `limits.min_spacing_m` | no | Minimum 3-D distance between any two drones at any time (FAIL). A `.skyc` file's own `minDistance` is also checked, as a warning level only (WARN). | C5 |
| `limits.max_altitude_m` | no | Ceiling above the origin altitude | C3 |
| `limits.min_nav_altitude_m` | no | C5 skips any pair where either drone is below this height, in metres of show Z above the show origin (same meaning as Skybrush `minNavAltitude`). If not set, a `.skyc` file's own `minNavAltitude` is used. If neither is set, 2.5 m is used (Skybrush firmware's default `SHOW_TAKEOFF_ALT`). Set it to 0 to check every pair. The report says which was used and how many pair-samples were skipped. | C5 |

**Notes**
- A missing limit or area makes that part of the check **WARN** ("not checked") rather than PASS.
- Only venue limits can FAIL. Limits in the show file's `settings.validation` are the designer's warning levels and can only WARN.
- Polygons may be listed clockwise or counter-clockwise. They must not self-intersect.
- The venue is the source of truth. The show is placed into it using the **show file's own origin and heading**; the venue's values are used only when the file has none, and the report says when that happened.
