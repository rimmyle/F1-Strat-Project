# Repository Guidelines

## Project Structure & Module Organization
This repository is a small Flask app. The main application logic lives in `app.py`. HTML templates are in `templates/`, with the main UI in `templates/index.html` and the error pages in `templates/error.html`. Static assets such as team logos and driver headshots live under `static/`. FastF1 data is cached locally in `.fastf1-cache/` and should not be committed.

## Build, Test, and Development Commands
- `python -m venv .venv` - create a local virtual environment.
- `.\.venv\Scripts\Activate.ps1` - activate the environment on Windows.
- `pip install -r requirements.txt` - install Flask and FastF1 dependencies.
- `python app.py` - start the app locally. It binds to `127.0.0.1` and defaults to port `5001` unless `PORT` is set.

## Coding Style & Naming Conventions
Use standard Python style: 4-space indentation, `snake_case` for functions and variables, and clear descriptive names for route helpers and graph builders. Keep template IDs and CSS class names consistent with existing patterns in `index.html`. Prefer small, targeted edits over broad rewrites because the app logic and UI are tightly coupled.

## Testing Guidelines
There is no automated test suite in the repository. Validate changes by running the app and checking the affected routes in the browser, especially `/`, `/session`, `/session-status`, `/results`, `/data`, `/lap`, and `/strategy`, plus any graph rendering or loading-state changes. If you add tests, place them alongside the relevant Python logic and name them clearly, such as `test_*.py`.

## Commit & Pull Request Guidelines
Recent commit history uses short imperative summaries like `Fix race overview and data selection` or `Update qualifying timeline and loading behavior`. Follow that style: concise, action-focused, and specific. Pull requests should explain the user-visible change, mention any data or cache implications, and include screenshots or short notes for UI updates.

## Configuration & Cache Notes
The app depends on FastF1 data and may behave differently when cache files are present. If debugging stale results, remove local cache contents under `.fastf1-cache/` rather than changing the source code first. Keep generated cache, log, and session artifact files out of commits unless they are intentionally part of the repo.

## Current App Behavior Notes
- `/` and `/session` are the session entry points, with `/session-status` polling the background loader.
- `/results` shows the race overview and qualifying tables/graphs once session data is ready.
- On race and sprint sessions, clicking a driver from `/data` now opens `/lap` directly; `/lap` remains the dedicated lap detail route.
- Clicking a stint opens `/lap` for that stint, and `/lap` defaults to a representative lap when only `driver` and `stint` are present.
- `/results`, `/data`, and `/strategy` now share the same session-view bootstrap helper so loading state, session badges, and fallback errors stay aligned.
- The non-qualifying driver picker in `templates/index.html` is collapsed to driver rows only; stint graphs now live in the lap view at the top of the page.
- Pit strategy graph stint bars now link into `/lap` with the selected driver and stint so the representative lap opens immediately on arrival.
- Race position driver clicks now route into `/lap` as well, landing on the top stint panel for the selected driver.
- Session results rows in the race sidebar now open `/lap` for the selected driver and land on the top stint panel too.
- The lap page keeps the stint graph in the top panel beside the driver image/name and lap summary, with a compact stint-bar strip in the same left column stacked vertically instead of scrolling horizontally; those stint cards use wider minimum widths so the stint and lap-count labels stay on one line, the top panel now gives the graph more width again while keeping the bar cards readable, and the stint graph axis labels now show plain lap numbers on X plus `Lap` and `Time` axis titles while the Y-axis labels render as `m:ss.mmm` instead of raw seconds. The track map now sits in a single full-width support card with the primary telemetry and secondary telemetry flanking the map inside the same viewport pane so the gap-to-ahead chart stays visible, and the lap-record metrics now live in the top-right of the hero card instead of a separate lower pane.
- The lap page stint dotline graph now badges out laps and in laps directly on the points, using `OUT`, `IN`, or a paired `OUT` + `IN` marker for combined pit-transition laps.
- The lap page stint dotline graph now marks personal-best laps with a compact `PB` badge on the point, while the lap record pane no longer shows a separate `Personal Best` metric.
- The lap page stint stack now inserts a dedicated pit-time bar between consecutive stint cards so pit loss is visible directly in the left column, and the pit label uses a compact duration format without a leading zero minute when the stop is under one minute.
- The lap page stint cards now show `(Fresh Tyre)` next to the stint number when the stint started on fresh rubber, and the lap record pane no longer repeats that `Fresh Tyre` field as a separate metric.
- The hero-card lap summary badges are now vertically tightened so the value tiles sit flush instead of leaving extra bottom buffer, and the separate `FastF1 Generated` badge has been removed from the lap record metrics.
- The lap page pit-time bars now compare each stop against the average pit time of the other drivers in the race, but the visible bar text is kept minimal: it shows the pit stop time and a signed delta line only; if the stint cache is cold on `/lap`, the route now warms the missing race-driver stints from the current session before calculating that average so the comparison still shows up after a restart, the lap-panel grid keeps the pit stack and graph in separate contained columns so the pit UI does not crowd the graph, and the left lap-panel stack is centered as a narrower column inside its partition.
- The track map on `/lap` now uses a looser internal map margin and badge clamping so sector and speed markers keep more breathing room from the SVG edges and are less likely to clip, and the racing line itself is color-coded by acceleration and deceleration derived from the speed trace instead of being rendered as a single fixed-color stroke; the separate track-map legend row has been removed from the card. The track map now lives in a single full-width support card, with the telemetry triplets stacked as a full-width row above the map and another full-width row below it, and the SVG height has been increased again so the map reads larger inside that viewport.
- Telemetry on `/lap` now renders as two embedded card rows inside the track card: speed/throttle/brake above the map and RPM/gear/gap below it; each set is laid out as three smaller side-by-side cards with synchronized live carets, the telemetry chart x-axis labels and range text render as `m:ss` instead of raw seconds, and the `Gap to Driver Ahead` trace is converted from distance to an estimated time gap in seconds while staying synced to the racing-line marker on the track map.
- The `Gap Ahead` telemetry badge now appends the currently-ahead driver's abbreviation in parentheses by sampling the `DriverAhead` telemetry channel and matching it against session results.
- The telemetry speed chart no longer overlays MAX/MIN callouts; the shared scrubber and per-row value pills are the only live indicators in that pane, and the telemetry chart carets now render as a line and dot only with no triangular tip.
- The hero-card lap metrics now render human-readable labels like `Green`, `Yellow Flag`, `Safety Car`, `Virtual Safety Car`, or `Red Flag` for track status instead of the raw FastF1 status code.
- The hero-card lap metrics no longer show the old compound or speed-trap fields for `Speed I1`, `Speed I2`, `Speed FL`, or `Speed ST`; the remaining lap-level fields stay in the compact badge cluster.
- The main page shell is intentionally wider than the original layout so the lap and results panes can use more horizontal space.
- Qualifying graphs use the chained phase logic from the run list, with Q1/Q2/Q3 windows of `18/15/12` minutes and sprint qualifying windows of `12/10/8` minutes.
- Qualifying run anchors use pit-out timing for out-laps when available.
- 404 and 500 errors render the dedicated `templates/error.html` page.
