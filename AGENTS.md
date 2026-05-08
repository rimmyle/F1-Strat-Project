# Repository Guidelines

## Project Structure & Module Organization
This repository is a small Flask app. The main application logic lives in `app.py`. HTML templates are in `templates/`, with the primary UI in `templates/index.html`. Static assets such as team logos and driver headshots live under `static/`. FastF1 data is cached locally in `.fastf1-cache/` and should not be committed.

## Build, Test, and Development Commands
- `python -m venv .venv` - create a local virtual environment.
- `.\.venv\Scripts\Activate.ps1` - activate the environment on Windows.
- `pip install -r requirements.txt` - install Flask and FastF1 dependencies.
- `python app.py` - start the app locally. It binds to `127.0.0.1` and defaults to port `5001` unless `PORT` is set.

## Coding Style & Naming Conventions
Use standard Python style: 4-space indentation, `snake_case` for functions and variables, and clear descriptive names for route helpers and graph builders. Keep template IDs and CSS class names consistent with existing patterns in `index.html`. Prefer small, targeted edits over broad rewrites because the app logic and UI are tightly coupled.

## Testing Guidelines
There is no automated test suite in the repository. Validate changes by running the app and checking the affected routes in the browser, especially session selection, race overview/data views, qualifying tabs, and any graph rendering changes. If you add tests, place them alongside the relevant Python logic and name them clearly, such as `test_*.py`.

## Commit & Pull Request Guidelines
Recent commit history uses short imperative summaries like `Fix race overview and data selection` or `Update qualifying timeline and loading behavior`. Follow that style: concise, action-focused, and specific. Pull requests should explain the user-visible change, mention any data or cache implications, and include screenshots or short notes for UI updates.

## Configuration & Cache Notes
The app depends on FastF1 data and may behave differently when cache files are present. If debugging stale results, remove local cache contents under `.fastf1-cache/` rather than changing the source code first.
