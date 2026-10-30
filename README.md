# Keep flight replay timing accurate when reducing trajectory storage

Flight replay and reporting tools can draw a convincing route while placing an aircraft at the wrong point at a given time. This study tests whether reducing trajectory points preserves route length and time spent inside defined zones, alongside the point-record storage reduction.

**Finding: under the authored budgets, keep the original point records. Release checks are still in progress.** Every tested setting that removes points fails at least one development run, so the selector keeps all points. The [frozen final comparison](FINAL_NOTE.md) applies that choice to 17 different aircraft; all other settings remain descriptive comparisons.

| Final setting | Point-record bytes retained | Runs failing a fixed budget |
|---|---:|---:|
| Keep every point, selected before final scores | 100% | 0 / 65 |
| Every fourth point | 25.3% | 61 / 65 |
| Geometric tolerance 25 m | 8.1% | 65 / 65 |
| Time-synchronous tolerance 10 m | 33.0% | 12 / 65 |

The authored limits are 25 m time-aligned position error, 1% horizontal route-length error and 2 seconds on two zone queries, applied to each run. Geometric 25 m reaches 1,038.37 m of time-aligned error. Time-synchronous 10 m meets position/timing limits but fails route length on twelve final runs. These example requirements do not rule out another grid or application; the route metric preserves a processed reference, not proven physical motion. The licensed TrajAir slice contains 106 usable runs from 30 aircraft, with 13 development and 17 final aircraft. [Source checks](SOURCE_BINDING.md) retain the exact source, split and exclusions. Its tracks are filtered and interpolated observations, not airline schedules or a live safety system. Aircraft identifiers and coordinate records stay outside Git.

The [protocol](PROTOCOL.md) defines eleven retained configurations, simple baselines, aircraft-separated development and final evaluation, fixed spatial/timing/route budgets, and independent source and numerical checks. The [source plan](data/source_plan.json) fixes the archive and scene-selection rule before retrieval. [Source rights and privacy](DATA_RIGHTS.md) describe attribution and the publication boundary.

Try the [offline implementation exercise](examples/README.md): a straight route can keep its length while replaying a zone exit 75 seconds early. The teaching records are authored, separate from the aircraft benchmark. No new compression algorithm, actual airline delay, production adoption, safety guarantee or financial saving is claimed.
