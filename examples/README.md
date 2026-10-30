# Try the query checks

These three CSVs are authored teaching records. They contain no measured aircraft data. Coordinates are metres in a made-up frame and time is elapsed integer seconds.

~~~powershell
python src/exercise.py examples/safe-simple-motion.csv --method geometric --value 25
python src/exercise.py examples/geometry-hides-time.csv --method geometric --value 25
python src/exercise.py examples/incomplete-time.csv --method geometric --value 25
~~~

The first case moves at a constant 20 m/s, so retaining its two endpoints reproduces the supplied positions and queries. The second stays still for 100 seconds and then moves at 40 m/s: the geometric line and route length remain exact, while replaying the endpoint chord places its first exit from the 1 km zone 75 seconds early. The third omits an elapsed time and returns an unavailable assessment. These cases teach a mechanism; they are not additional empirical aircraft or safety validation.

Use the same CSV columns for a local exercise: elapsed_seconds,x_m,y_m,z_m. The command reports numerical checks and preserves your source file. It writes no coordinate payload, makes no network request and controls no device. The study's 25 m, 1% route and 2 second limits are authored examples; their suitability for another application requires its own decision requirements.
