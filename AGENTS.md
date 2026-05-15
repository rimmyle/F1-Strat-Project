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
- `/data` remains the driver and stint selection page, while `/lap` is the dedicated lap detail route.
- Clicking a stint should open `/lap` for that stint, and `/lap` now defaults to a representative lap when only `driver` and `stint` are present.
- `/results`, `/data`, and `/strategy` now share the same session-view bootstrap helper so loading state, session badges, and fallback errors stay aligned.
- The non-qualifying driver picker in `templates/index.html` is collapsed to driver rows only; stint graphs now live in the lap view at the top of the page.
- Pit strategy graph stint bars now link into `/lap` with the selected driver and stint so the representative lap opens immediately on arrival.
- Race position driver clicks now route into `/lap` as well, landing on the top stint panel for the selected driver.
- Session results rows in the race sidebar now open `/lap` for the selected driver and land on the top stint panel too.
- The lap page is currently arranged as a top stint graph, a middle support panel with the track map on the left and primary telemetry on the right, a secondary telemetry block below, and the lap record at the bottom.
- Telemetry on `/lap` is split into primary charts for Speed, Throttle, and Brake, with the remaining charts rendered in the secondary block.
- The main page shell is intentionally wider than the original layout so the lap and results panes can use more horizontal space.
- Qualifying graphs use the chained phase logic from the run list, with Q1/Q2/Q3 windows of `18/15/12` minutes and sprint qualifying windows of `12/10/8` minutes.
- Qualifying run anchors use pit-out timing for out-laps when available.
- 404 and 500 errors render the dedicated `templates/error.html` page.
