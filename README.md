# Keep flight replay timing accurate when reducing trajectory storage

Flight replay and reporting tools can draw a convincing route while placing an aircraft at the wrong point at a given time. This study tests whether reducing trajectory points preserves route length and time spent inside defined zones, alongside the point-record storage reduction.

**Current state: source and analysis protocol fixed; no trajectory cohort downloaded, no measured result or release.** The planned source is a bounded week of the licensed TrajAir research dataset. Its processed tracks are interpolated observations around one general aviation airport, not airline schedules or a live safety system. Aircraft identities and coordinate records will remain outside this repository.

The [protocol](PROTOCOL.md) defines eleven retained configurations, simple baselines, aircraft-separated development and final evaluation, fixed spatial/timing/route budgets, and independent source and numerical checks. The [source plan](data/source_plan.json) fixes the archive and scene-selection rule before retrieval. [Source rights and privacy](DATA_RIGHTS.md) describe attribution and the publication boundary.

The practical output will be a reproducible benchmark and an offline implementation exercise: check the actual queries a compressed track must support before choosing a smaller representation. No new compression algorithm, actual airline delay, production adoption, safety guarantee or financial saving is claimed.
