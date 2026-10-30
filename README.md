# Keep flight replay timing accurate when reducing trajectory storage

Flight replay and reporting tools can draw a convincing route while placing an aircraft at the wrong point at a given time. This study tests whether reducing trajectory points preserves route length and time spent inside defined zones, alongside the point-record storage reduction.

**Current state: development checks pass; final-aircraft evaluation and release are pending.** Only keeping every point meets all fixed budgets across the 41 development runs. Every tested setting that removes points fails at least one run. [The full development record](DEVELOPMENT_NOTE.md) retains the failures; the chosen fallback will be frozen before final scoring.

| Development setting | Point-record bytes retained | Runs failing a fixed budget |
|---|---:|---:|
| Keep every point | 100% | 0 / 41 |
| Every fourth point | 25.4% | 36 / 41 |
| Geometric tolerance 25 m | 6.3% | 37 / 41 |
| Time-synchronous tolerance 10 m | 29.3% | 10 / 41 |

The authored limits are 25 m time-aligned position error, 1% horizontal route-length error and 2 seconds on two zone queries, applied to each run. These are example decision requirements. This result does not rule out another grid or application. The licensed TrajAir slice contains 106 usable runs from 30 aircraft, with 17 aircraft reserved for final evaluation. [Source checks](SOURCE_BINDING.md) retain the exact source, split and exclusions. Its tracks are filtered and interpolated observations, not airline schedules or a live safety system. Aircraft identifiers and coordinate records stay outside Git.

The [protocol](PROTOCOL.md) defines eleven retained configurations, simple baselines, aircraft-separated development and final evaluation, fixed spatial/timing/route budgets, and independent source and numerical checks. The [source plan](data/source_plan.json) fixes the archive and scene-selection rule before retrieval. [Source rights and privacy](DATA_RIGHTS.md) describe attribution and the publication boundary.

Try the [offline implementation exercise](examples/README.md): a straight route can keep its length while replaying a zone exit 75 seconds early. The teaching records are authored, separate from the aircraft benchmark. No new compression algorithm, actual airline delay, production adoption, safety guarantee or financial saving is claimed.
