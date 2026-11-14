# Cricket through SQL

[01 — Economy needs runs and balls](01_economy_needs_runs_and_balls.ipynb) reconstructs bowling economy from a real Cricsheet match, reconciles the worker's scorecard, and compares correct rates with a tempting row-count shortcut. Two charts show the rate discrepancy and its numerator/denominator components. The final experiment changes `START_OVER` from 1 to 16.

The saved default uses **recorded public data**, not a simulated match. The recording is the unchanged JSON for IPL final 1535465 (31 May 2026), extracted from the public IPL archive on 14 November 2026 at 05:58 UTC. [Provenance](data/provenance.json) records the exact source URL, time and SHA-256 for both archive and member. Data attribution: [Cricsheet](https://cricsheet.org/), [Open Data Commons Attribution License 1.0](https://opendatacommons.org/licenses/by/1-0/). No affiliation with the competition or teams is implied.

## Run from this folder

Prerequisites: [uv](https://docs.astral.sh/uv/), Git, and internet access for the first dependency/worker/extension installation. Python is provisioned by uv. The literal ATTACH command fetches public worker revision `fb372ed20bdc9918501ee78f7ca61a00f484c1ae` with Python 3.13 and `vgi-python==0.51.1`. The notebook environment independently pins published Haybarn `2.0.0rc11`. It needs no root source tree, root environment, API credentials or locally built worker. The exact launch was verified through Haybarn on macOS arm64; other platforms were not tested.

Copy **this entire notebooks directory**, including `data/`, `assets/`, the helpers and lockfile. From the copied folder:

```sh
uv sync --frozen
uv run --frozen python execute.py
```

The runner starts a fresh temporary kernel and saves the notebook only after all cells succeed. A failed execution preserves the previous saved result. Parameter overrides require a separate output directory, and the runner copies the branding, data and documentation companions there:

```sh
uv run --frozen python execute.py \
  --parameters '{"START_OVER":16}' --output-dir validation/overs-16
```

For an interactive notebook editor, register this environment once and select **Python (vgi-cricket notebooks)**:

```sh
uv run --frozen python -m ipykernel install --user \
  --name cricket-examples --display-name 'Python (vgi-cricket notebooks)'
```

Edit the visible parameter cell, restart the kernel, and run all cells. Helpers locate their assets beside themselves, and the runner uses this folder as its working directory. No editor automation is required.

## Recording boundary and source refresh

`recorded_api.py` verifies the member digest and repackages exactly one unchanged match into a zip, then serves it on an ephemeral loopback port. It supports only `/downloads/ipl_json.zip`. The notebook uses `CRICKET_CRICSHEET_BASE_URL`, an existing worker option. This deliberately incomplete archive is suitable only for match 1535465; **do not calculate season totals from it**. The other public APIs are not used.

Set `DATA_MODE="live"` to download the public IPL archive and select the same match. Setup clears inherited endpoint overrides in both modes and sets `CRICKET_CACHE_DIR=off`; live execution cannot silently substitute the recording or a stale disk cache. The worker still caches the archive within its process for repeated queries. Upstream corrections can change results. To refresh separately:

```sh
uv run --frozen python execute.py \
  --parameters '{"DATA_MODE":"live"}' --output-dir validation/live
```

The default recording is not overwritten by live mode. Updating it is an explicit provenance operation: retrieve the archive, retain the chosen member unchanged, update both digests and retrieval metadata, and rerun all checks. To upgrade the worker, edit the literal Git revision in ATTACH, verify its published SDK compatibility, and rerun default, experiment and live checks. Root worker locks remain independent.

## Interpretation

The notebook excludes super overs and uses integer legal-ball counts. Cricket notation such as `3.4` is never treated as a decimal duration. Bowling economy uses bat runs, wides and no-ball runs; byes, leg byes and penalties are excluded. Full-match reconciliation compares two calculations from the same underlying source, not independently scored match data.

Phase comparisons retain exposure labels and show zero corrections explicitly. A shorter phase is not evidence of changed ability; opponent and match context are uncontrolled. A bowler with no legal balls causes an explicit stop instead of a zero or infinite rate. Source structure or component inconsistencies stop the notebook before charts are drawn.

Generated experiments, charts and logs stay under ignored `validation/`. The approved presentation rules are in [STANDARD.md](STANDARD.md).
