from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import re
from threading import Lock

import fastf1
import pandas as pd
import numpy as np
from flask import Flask, render_template, request


app = Flask(__name__)
cache_dir = Path(__file__).with_name(".fastf1-cache")
cache_dir.mkdir(exist_ok=True)
fastf1.Cache.enable_cache(str(cache_dir))

SESSION_CACHE = {}
SESSION_JOBS = {}
SESSION_OBJECTS = {}
SCHEDULE_CACHE = {}
SESSION_LOCK = Lock()
SESSION_EXECUTOR = ThreadPoolExecutor(max_workers=2)
YEAR_MIN = 2018
YEAR_MAX = datetime.now().year
SESSION_CHOICES = [
    ("FP1", "Practice 1"),
    ("FP2", "Practice 2"),
    ("FP3", "Practice 3"),
    ("SQ", "Sprint Qualifying"),
    ("SS", "Sprint Shootout"),
    ("S", "Sprint"),
    ("Q", "Qualifying"),
    ("R", "Race"),
]
SESSION_LABELS = dict(SESSION_CHOICES)
TEAM_BADGE_STYLES = {
    "Mercedes": ("ME", "#00d2be"),
    "Red Bull Racing": ("RB", "#1e41ff"),
    "Ferrari": ("FE", "#dc0000"),
    "McLaren": ("MC", "#ff8700"),
    "Aston Martin": ("AM", "#006f62"),
    "Alpine": ("AL", "#0090ff"),
    "Williams": ("WI", "#00a3e0"),
    "Haas F1 Team": ("HA", "#b6babd"),
    "Alfa Romeo": ("AR", "#900000"),
    "AlphaTauri": ("AT", "#2b4562"),
    "RB F1 Team": ("RB", "#2b4562"),
    "Kick Sauber": ("KS", "#52e252"),
    "Stake F1 Team Kick Sauber": ("KS", "#52e252"),
}


def _clean_value(value):
    if pd.isna(value):
        return "-"
    if isinstance(value, pd.Timedelta):
        total_seconds = value.total_seconds()
        hours, remainder = divmod(int(total_seconds), 3600)
        minutes, seconds = divmod(remainder, 60)
        millis = int(round((total_seconds - int(total_seconds)) * 1000))
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{millis:03d}"
    if isinstance(value, (int, float)) and float(value).is_integer():
        return str(int(value))
    if hasattr(value, "isoformat") and not isinstance(value, str):
        return value.isoformat()
    return str(value)


def _session_rows(session):
    results = getattr(session, "results", None)
    if results is None or results.empty:
        return []

    preferred_columns = [
        "Position",
        "Abbreviation",
        "FullName",
        "TeamName",
        "Q1",
        "Q2",
        "Q3",
        "Time",
        "Status",
        "Points",
        "Laps",
    ]
    columns = [column for column in preferred_columns if column in results.columns]
    rows = results.loc[:, columns].head(10).copy()
    return [
        {column: _clean_value(row[column]) for column in columns}
        for _, row in rows.iterrows()
    ]


def _session_label(session_code):
    return SESSION_LABELS.get(str(session_code).strip().upper(), str(session_code))


def _strategy_summary(data):
    rows = data.get("rows", []) if data else []
    leader = rows[0] if rows else {}
    return {
        "leader": leader.get("FullName", "-"),
        "leader_team": leader.get("TeamName", "-"),
        "leader_time": leader.get("Time", "-"),
        "entries": len(rows),
    }


def _data_summary(data):
    if not data:
        return None

    rows = data.get("rows", [])
    return {
        "event_name": data.get("event_name", "-"),
        "session_name": data.get("session_name", "-"),
        "event_country": data.get("event_country", "-"),
        "event_round": data.get("event_round", "-"),
        "session_date": data.get("session_date", "-"),
        "driver_count": len(rows),
        "leader": rows[0].get("FullName", "-") if rows else "-",
    }


def _team_badge(team_name):
    normalized = _clean_value(team_name)
    badge_text, badge_color = TEAM_BADGE_STYLES.get(normalized, (None, None))
    if badge_text is None:
        letters = [part[0] for part in re.findall(r"[A-Za-z0-9]+", normalized) if part]
        badge_text = "".join(letters[:2]).upper() if letters else "TM"
    if badge_color is None:
        badge_color = "#64748b"
    return badge_text, badge_color


def _driver_options(session):
    results = getattr(session, "results", None)
    if results is None or results.empty:
        return []

    columns = [col for col in ["DriverNumber", "Abbreviation", "FullName", "TeamName", "Position"] if col in results.columns]
    if not columns:
        return []

    ordered = results.loc[:, columns].copy()
    if "Position" in ordered.columns:
        ordered = ordered.sort_values(by="Position")

    options = []
    for _, row in ordered.iterrows():
        driver_number = _clean_value(row.get("DriverNumber", ""))
        abbreviation = _clean_value(row.get("Abbreviation", driver_number))
        full_name = _clean_value(row.get("FullName", abbreviation))
        team_name = _clean_value(row.get("TeamName", "-"))
        team_badge_text, team_badge_color = _team_badge(team_name)
        options.append(
            {
                "value": driver_number,
                "abbreviation": abbreviation,
                "full_name": full_name,
                "team_name": team_name,
                "team_badge_text": team_badge_text,
                "team_badge_color": team_badge_color,
                "label": f"{abbreviation} - {full_name}",
            }
        )
    seen = set()
    deduped = []
    for option in options:
        if option["value"] in seen:
            continue
        seen.add(option["value"])
        deduped.append(option)
    return deduped


def _lap_options(session, driver_number):
    if not driver_number:
        return []

    driver_laps = session.laps.pick_drivers(driver_number)
    if driver_laps is None or driver_laps.empty:
        return []

    valid = driver_laps[driver_laps["LapTime"].notna()].copy()
    if valid.empty:
        valid = driver_laps.copy()

    valid = valid.sort_values(by=["LapNumber", "Time"])
    options = []
    for _, row in valid.iterrows():
        lap_number = row.get("LapNumber")
        lap_time = row.get("LapTime")
        compound = _clean_value(row.get("Compound", "-"))
        value = f"{_clean_value(driver_number)}:{_clean_value(lap_number)}"
        label = f"Lap {_clean_value(lap_number)} - {_clean_value(lap_time)} - {compound}"
        options.append({"value": value, "label": label})
    return options


def _parse_lap_key(lap_key):
    if not lap_key or ":" not in lap_key:
        return None, None
    driver_number, lap_number = lap_key.split(":", 1)
    return driver_number.strip(), lap_number.strip()


def _parse_stint_value(stint_value):
    if stint_value in (None, ""):
        return None
    try:
        return int(stint_value)
    except (TypeError, ValueError):
        return None


def _resolve_driver(session, driver_number):
    drivers = _driver_options(session)
    if driver_number and any(option["value"] == driver_number for option in drivers):
        return driver_number
    if drivers:
        return drivers[0]["value"]
    return None


def _resolve_lap(session, driver_number, lap_key):
    if not driver_number:
        return None

    driver_laps = session.laps.pick_drivers(driver_number)
    if driver_laps is None or driver_laps.empty:
        return None

    target_driver, target_lap = _parse_lap_key(lap_key)
    if target_driver == driver_number and target_lap is not None:
        try:
            target_lap_number = float(target_lap)
        except ValueError:
            target_lap_number = None
        if target_lap_number is not None:
            match = driver_laps[driver_laps["LapNumber"] == target_lap_number]
            if not match.empty:
                return match.iloc[0]

    fastest = driver_laps.pick_fastest()
    if fastest is not None and not fastest.empty:
        return fastest

    valid = driver_laps[driver_laps["LapTime"].notna()]
    if not valid.empty:
        return valid.iloc[0]
    return driver_laps.iloc[0]


def _telemetry_rows(lap, limit=240):
    telemetry = lap.get_telemetry(frequency="original")
    telemetry = telemetry.reset_index(drop=True)
    columns = [
        column
        for column in [
            "SessionTime",
            "Time",
            "Distance",
            "Speed",
            "Throttle",
            "Brake",
            "RPM",
            "nGear",
            "DriverAhead",
            "DistanceToDriverAhead",
            "DRS",
            "X",
            "Y",
            "Status",
        ]
        if column in telemetry.columns
    ]
    rows = telemetry.loc[:, columns].head(limit).copy()
    return columns, [
        {column: _clean_value(row[column]) for column in columns}
        for _, row in rows.iterrows()
    ], telemetry


def _lap_summary(lap, telemetry):
    summary = {
        "driver": _clean_value(lap.get("Driver", "-")),
        "team": _clean_value(lap.get("Team", "-")),
        "lap_number": _clean_value(lap.get("LapNumber", "-")),
        "lap_time": _clean_value(lap.get("LapTime", "-")),
        "compound": _clean_value(lap.get("Compound", "-")),
        "stint": _clean_value(lap.get("Stint", "-")),
        "tyre_life": _clean_value(lap.get("TyreLife", "-")),
        "samples": len(telemetry),
    }
    if "Speed" in telemetry.columns and not telemetry.empty:
        summary["max_speed"] = _clean_value(telemetry["Speed"].max())
    else:
        summary["max_speed"] = "-"
    return summary


def _lap_record(lap, telemetry):
    ahead_driver = "-"
    if "DriverAhead" in telemetry.columns:
        series = telemetry["DriverAhead"].dropna().astype(str)
        series = series[series.str.strip() != ""]
        if not series.empty:
            ahead_driver = series.iloc[-1].strip()

    gap_ahead = "-"
    if "DistanceToDriverAhead" in telemetry.columns:
        gap_series = telemetry["DistanceToDriverAhead"].dropna()
        if not gap_series.empty:
            gap_ahead = f"{float(gap_series.iloc[-1]):.3f} m"

    fields = [
        ("driver", "Driver", _clean_value(lap.get("Driver", "-"))),
        ("team", "Team", _clean_value(lap.get("Team", "-"))),
        ("lap_number", "Lap Number", _clean_value(lap.get("LapNumber", "-"))),
        ("lap_time", "Lap Time", _clean_value(lap.get("LapTime", "-"))),
        ("stint", "Stint", _clean_value(lap.get("Stint", "-"))),
        ("sector_1_time", "Sector 1", _clean_value(lap.get("Sector1Time", "-"))),
        ("sector_2_time", "Sector 2", _clean_value(lap.get("Sector2Time", "-"))),
        ("sector_3_time", "Sector 3", _clean_value(lap.get("Sector3Time", "-"))),
        ("speed_i1", "Speed I1", _clean_value(lap.get("SpeedI1", "-"))),
        ("speed_i2", "Speed I2", _clean_value(lap.get("SpeedI2", "-"))),
        ("speed_fl", "Speed FL", _clean_value(lap.get("SpeedFL", "-"))),
        ("speed_st", "Speed ST", _clean_value(lap.get("SpeedST", "-"))),
        ("compound", "Compound", _clean_value(lap.get("Compound", "-"))),
        ("tyre_life", "Tyre Life", _clean_value(lap.get("TyreLife", "-"))),
        ("fresh_tyre", "Fresh Tyre", _clean_value(lap.get("FreshTyre", "-"))),
        ("track_status", "Track Status", _clean_value(lap.get("TrackStatus", "-"))),
        ("position", "Position", _clean_value(lap.get("Position", "-"))),
        ("deleted", "Deleted", _clean_value(lap.get("Deleted", "-"))),
        ("deleted_reason", "Deleted Reason", _clean_value(lap.get("DeletedReason", "-"))),
        ("fastf1_generated", "FastF1 Generated", _clean_value(lap.get("FastF1Generated", "-"))),
        ("is_personal_best", "Personal Best", _clean_value(lap.get("IsPersonalBest", "-"))),
        ("is_accurate", "Accurate", _clean_value(lap.get("IsAccurate", "-"))),
        ("driver_ahead", "Driver Ahead", ahead_driver),
        ("distance_to_driver_ahead", "Gap to Driver Ahead", gap_ahead),
    ]

    return fields


def _race_stints(session, driver_number, session_code):
    if not session or not driver_number or str(session_code).strip().upper() != "R":
        return None

    driver_laps = session.laps.pick_drivers(driver_number)
    if driver_laps is None or driver_laps.empty:
        return None

    if "Stint" not in driver_laps.columns:
        return None

    stints = []
    ordered = driver_laps[driver_laps["LapNumber"].notna()].copy()
    if ordered.empty:
        return None

    ordered = ordered.sort_values(by=["LapNumber", "Time"])
    for stint_number, stint_laps in ordered.groupby("Stint", dropna=True):
        stint_laps = stint_laps.sort_values(by=["LapNumber", "Time"])
        if stint_laps.empty:
            continue

        compound_series = stint_laps["Compound"].dropna().astype(str) if "Compound" in stint_laps.columns else pd.Series([], dtype=str)
        compound = compound_series[compound_series.str.strip() != ""].iloc[0] if not compound_series.empty else "-"

        fresh_series = stint_laps["FreshTyre"].dropna() if "FreshTyre" in stint_laps.columns else pd.Series([], dtype=object)
        fresh_tyre = bool(fresh_series.iloc[0]) if not fresh_series.empty and pd.notna(fresh_series.iloc[0]) else False

        lap_numbers = stint_laps["LapNumber"].dropna().astype(int).tolist()
        laps = []
        for _, lap_row in stint_laps.iterrows():
            lap_number = lap_row.get("LapNumber")
            if pd.isna(lap_number):
                continue
            lap_number_int = int(lap_number)
            lap_time = lap_row.get("LapTime")
            laps.append(
                {
                    "lap_number": lap_number_int,
                    "lap_time": _clean_value(lap_time),
                    "value": f"{_clean_value(driver_number)}:{lap_number_int}",
                    "compound": _clean_value(lap_row.get("Compound", "-")),
                    "fresh_tyre": bool(lap_row.get("FreshTyre", False)) if pd.notna(lap_row.get("FreshTyre", False)) else False,
                }
            )
        stints.append(
            {
                "stint": int(stint_number) if pd.notna(stint_number) else 0,
                "start_lap": min(lap_numbers) if lap_numbers else None,
                "end_lap": max(lap_numbers) if lap_numbers else None,
                "lap_count": len(stint_laps),
                "compound": compound,
                "fresh_tyre": fresh_tyre,
                "laps": laps,
            }
        )

    if not stints:
        return None

    return {
        "driver": _clean_value(driver_laps.iloc[0].get("Driver", "-")),
        "stints": stints,
        "lap_total": int(sum(item["lap_count"] for item in stints)),
    }


def _telemetry_charts(lap):
    telemetry = lap.get_telemetry(frequency="original").reset_index(drop=True)
    if telemetry.empty or "Time" not in telemetry.columns:
        return []

    time_series = telemetry["Time"].dt.total_seconds()
    palette = [
        "#44c2ff",
        "#77f0d1",
        "#ffbf69",
        "#b794f4",
        "#63e6b2",
        "#ff8c8c",
        "#8bd3ff",
        "#c6ff7d",
    ]
    specs = [
        ("Speed", "Speed", "km/h", None),
        ("Throttle", "Throttle", "%", None),
        ("Brake", "Brake", "0 / 1", lambda value: 1 if bool(value) else 0),
        ("RPM", "RPM", "", None),
        ("nGear", "Gear", "", None),
        ("DistanceToDriverAhead", "Gap to Driver Ahead", "m", None),
    ]

    charts = []
    for index, (column, title, unit, transform) in enumerate(specs):
        if column not in telemetry.columns:
            continue

        points = []
        series = telemetry[column]
        for t_value, raw_value in zip(time_series, series):
            if pd.isna(t_value) or pd.isna(raw_value):
                continue

            value = transform(raw_value) if transform else raw_value
            if pd.isna(value):
                continue
            try:
                numeric_value = float(value)
            except (TypeError, ValueError):
                continue
            points.append([float(t_value), numeric_value])

        if not points:
            continue

        y_values = [point[1] for point in points]
        y_min = min(y_values)
        y_max = max(y_values)
        if y_min == y_max:
            y_min -= 1
            y_max += 1

        charts.append(
            {
                "id": column.lower(),
                "title": title,
                "unit": unit,
                "color": palette[index % len(palette)],
                "points": points,
                "x_min": points[0][0],
                "x_max": points[-1][0],
                "y_min": y_min,
                "y_max": y_max,
            }
        )

    return charts


def _rotate_xy(points, rotation_degrees):
    if not points:
        return []

    angle = np.deg2rad(rotation_degrees)
    rot = np.array([[np.cos(angle), np.sin(angle)], [-np.sin(angle), np.cos(angle)]])
    rotated = np.matmul(np.asarray(points, dtype=float), rot)
    return rotated.tolist()


def _track_map_payload(session, lap):
    try:
        circuit_info = session.get_circuit_info()
    except Exception:
        circuit_info = None

    if circuit_info is None:
        return None

    try:
        circuit_points = circuit_info.corners.loc[:, ["X", "Y"]].dropna().to_numpy().tolist()
        lap_pos = lap.get_pos_data()
    except Exception:
        return None

    if not circuit_points or lap_pos is None or lap_pos.empty:
        return None

    outline = circuit_points
    racing = lap_pos.loc[:, ["X", "Y"]].dropna().to_numpy().tolist()
    outline = _rotate_xy(outline, circuit_info.rotation)
    racing = _rotate_xy(racing, circuit_info.rotation)

    all_points = outline + racing
    if not all_points:
        return None

    focus_points = racing or all_points
    xs = [point[0] for point in focus_points]
    ys = [point[1] for point in focus_points]
    padding = max((max(xs) - min(xs)) * 0.02, (max(ys) - min(ys)) * 0.02, 8)

    return {
        "outline": outline,
        "racing": racing,
        "bounds": {
            "min_x": min(xs) - padding,
            "max_x": max(xs) + padding,
            "min_y": min(ys) - padding,
            "max_y": max(ys) + padding,
        },
        "rotation": circuit_info.rotation,
    }


def _resolve_context():
    year = request.args.get("year", "2024")
    gp = request.args.get("gp", "Bahrain Grand Prix")
    session_code = request.args.get("session", "R")

    try:
        year_int = int(year)
    except ValueError:
        year_int = YEAR_MAX

    year_int = max(YEAR_MIN, min(YEAR_MAX, year_int))
    year = str(year_int)

    try:
        schedule = _schedule_for_year(year_int)
    except Exception as exc:
        schedule = None
        schedule_error = str(exc)
    else:
        schedule_error = None

    selected_event = _selected_event(schedule, gp) if schedule is not None else None
    event_options = _event_options(schedule) if schedule is not None else []
    session_options = _session_options(selected_event)

    if selected_event is not None:
        gp = str(selected_event.EventName)
        if session_options and session_code.upper() not in {opt["value"] for opt in session_options}:
            session_code = session_options[0]["value"]
    elif event_options:
        gp = event_options[0]["value"]
        if session_options:
            session_code = session_options[0]["value"]

    session_badge = f"{year} {gp} - {_session_label(session_code)}"
    if selected_event is not None:
        session_badge = f"{year} {selected_event.EventName} - {_session_label(session_code)}"

    return {
        "year": year,
        "year_int": year_int,
        "gp": gp,
        "session_code": session_code,
        "schedule": schedule,
        "schedule_error": schedule_error,
        "selected_event": selected_event,
        "event_options": event_options,
        "session_options": session_options,
        "session_badge": session_badge,
    }


def _session_key(year, gp, session_code):
    return (int(year), str(gp).strip(), str(session_code).strip().upper())


def _year_options():
    return [str(year) for year in range(YEAR_MAX, YEAR_MIN - 1, -1)]


def _schedule_for_year(year):
    year = int(year)
    with SESSION_LOCK:
        if year in SCHEDULE_CACHE:
            return SCHEDULE_CACHE[year]

    schedule = fastf1.get_event_schedule(year, include_testing=False)

    with SESSION_LOCK:
        SCHEDULE_CACHE[year] = schedule
    return schedule


def _event_options(schedule):
    return [
        {
            "value": str(event.EventName),
            "label": f"Round {int(event.RoundNumber)} - {event.EventName}",
        }
        for _, event in schedule.iterrows()
    ]


def _selected_event(schedule, gp):
    if schedule is None or schedule.empty:
        return None

    try:
        return schedule.get_event_by_name(gp)
    except Exception:
        return schedule.iloc[0]


def _session_options(event):
    if event is None:
        return []

    options = []
    for field_name in ("Session1", "Session2", "Session3", "Session4", "Session5"):
        session_name = event.get(field_name, "")
        if pd.isna(session_name) or not session_name:
            continue

        code = next(
            (abbr for abbr, name in SESSION_CHOICES if name == session_name),
            None,
        )
        if code is None:
            continue

        options.append({"value": code, "label": session_name})

    return options


def _event_from_session_data(year, gp):
    schedule = _schedule_for_year(year)
    event = _selected_event(schedule, gp)
    return schedule, event


def _load_session_data(year, gp, session_code):
    schedule, event = _event_from_session_data(year, gp)
    if event is None:
        raise ValueError(f"No event schedule found for {year}")

    session = event.get_session(session_code)
    session.load()
    key = _session_key(year, gp, session_code)
    with SESSION_LOCK:
        SESSION_OBJECTS[key] = session
    return {
        "status": "ready",
        "year": int(year),
        "gp": str(event.EventName),
        "session_name": session.name,
        "session_date": session.date.strftime("%Y-%m-%d %H:%M") if session.date else "-",
        "event_name": getattr(session.event, "EventName", gp),
        "event_country": getattr(session.event, "Country", "-"),
        "event_round": getattr(session.event, "RoundNumber", "-"),
        "rows": _session_rows(session),
    }


def _get_session_state(year, gp, session_code):
    key = _session_key(year, gp, session_code)

    with SESSION_LOCK:
        cached = SESSION_CACHE.get(key)
        if cached is not None:
            if cached.get("status") == "ready":
                return cached
            return {"status": "ready", **cached}

        future = SESSION_JOBS.get(key)
        if future is None:
            future = SESSION_EXECUTOR.submit(_load_session_data, *key)
            SESSION_JOBS[key] = future
            return {"status": "loading"}

    if future.done():
        try:
            result = future.result()
        except Exception as exc:
            result = {"status": "error", "message": str(exc)}
        else:
            if result.get("status") != "ready":
                result = {"status": "ready", **result}

        with SESSION_LOCK:
            SESSION_JOBS.pop(key, None)
            SESSION_CACHE[key] = result

        return result

    return {"status": "loading"}


def _session_object(year, gp, session_code):
    key = _session_key(year, gp, session_code)
    with SESSION_LOCK:
        session = SESSION_OBJECTS.get(key)
    if session is not None:
        return session

    session_state = _get_session_state(year, gp, session_code)
    if session_state.get("status") != "ready":
        return None

    schedule, event = _event_from_session_data(year, gp)
    if event is None:
        return None

    session = event.get_session(session_code)
    session.load()
    with SESSION_LOCK:
        SESSION_OBJECTS[key] = session
    return session


@app.route("/")
def index():
    return results()


@app.route("/session")
def session_selector():
    ctx = _resolve_context()
    return render_template(
        "index.html",
        page="session",
        data=None,
        error=ctx["schedule_error"],
        loading=False,
        view="session",
        strategy=None,
        session_badge=ctx["session_badge"],
        years=_year_options(),
        events=ctx["event_options"],
        sessions=ctx["session_options"],
        form_action="/results",
        form={
            "year": ctx["year"],
            "gp": ctx["gp"],
            "session": ctx["session_code"],
        },
    )


@app.route("/results")
def results():
    ctx = _resolve_context()
    session_state = _get_session_state(ctx["year"], ctx["gp"], ctx["session_code"])
    data = session_state if session_state.get("status") == "ready" else None
    loading = session_state.get("status") == "loading"
    error = session_state.get("message") if session_state.get("status") == "error" else None
    if ctx["schedule_error"] and not error:
        error = ctx["schedule_error"]
    strategy = _strategy_summary(data) if data else None
    session_badge = ctx["session_badge"]
    if data:
        session_badge = f"{data['year']} {data['event_name']} - {data['session_name']}"

    return render_template(
        "index.html",
        page="results",
        data=data,
        error=error,
        loading=loading,
        view="results",
        strategy=strategy,
        session_badge=session_badge,
        years=_year_options(),
        events=ctx["event_options"],
        sessions=ctx["session_options"],
        form_action="/results",
        form={
            "year": ctx["year"],
            "gp": ctx["gp"],
            "session": ctx["session_code"],
        },
    )


@app.route("/data")
def data():
    ctx = _resolve_context()
    session_state = _get_session_state(ctx["year"], ctx["gp"], ctx["session_code"])
    data_state = session_state if session_state.get("status") == "ready" else None
    loading = session_state.get("status") == "loading"
    error = session_state.get("message") if session_state.get("status") == "error" else None
    if ctx["schedule_error"] and not error:
        error = ctx["schedule_error"]
    session = _session_object(ctx["year"], ctx["gp"], ctx["session_code"]) if data_state else None
    driver_number = request.args.get("driver", "")
    lap_key = request.args.get("lap", "")
    stint_key = request.args.get("stint", "")
    lap_requested = bool(lap_key)
    stint_requested = bool(stint_key)
    driver_options = _driver_options(session) if session else []
    driver_number = _resolve_driver(session, driver_number) if session else None
    selected_driver_data = next((option for option in driver_options if option["value"] == driver_number), None)
    race_stints = _race_stints(session, driver_number, ctx["session_code"]) if session and driver_number else None
    selected_stint = _parse_stint_value(stint_key)
    selected_lap = None
    if session and driver_number:
        if lap_requested:
            selected_lap = _resolve_lap(session, driver_number, lap_key)
            if selected_lap is not None and selected_stint is None:
                selected_stint = _parse_stint_value(selected_lap.get("Stint", None))
        elif not stint_requested:
            selected_lap = _resolve_lap(session, driver_number, "")
    selected_stint_data = None
    if race_stints and race_stints.get("stints") and selected_stint is not None:
        selected_stint_data = next((item for item in race_stints["stints"] if item["stint"] == selected_stint), None)
    if selected_lap is not None:
        lap_value = f"{_clean_value(driver_number)}:{_clean_value(selected_lap.get('LapNumber', ''))}"
    else:
        lap_value = ""

    telemetry_columns = []
    telemetry_rows = []
    telemetry_summary = None
    lap_record = []
    telemetry_charts = []
    track_map = None
    if selected_lap is not None:
        telemetry_columns, telemetry_rows, telemetry = _telemetry_rows(selected_lap)
        telemetry_summary = _lap_summary(selected_lap, telemetry)
        lap_record = _lap_record(selected_lap, telemetry)
        telemetry_charts = _telemetry_charts(selected_lap)
        try:
            track_map = _track_map_payload(session, selected_lap) if session else None
        except Exception:
            track_map = None
    session_badge = ctx["session_badge"]
    if data_state:
        session_badge = f"{data_state['year']} {data_state['event_name']} - {data_state['session_name']}"

    return render_template(
        "index.html",
        page="data",
        data=data_state,
        error=error,
        loading=loading,
        view="data",
        strategy=_strategy_summary(data_state) if data_state else None,
        data_summary=_data_summary(data_state) if data_state else None,
        telemetry_summary=telemetry_summary,
        lap_record=lap_record,
        race_stints=race_stints,
        selected_stint=selected_stint,
        selected_stint_data=selected_stint_data,
        telemetry_columns=telemetry_columns,
        telemetry_rows=telemetry_rows,
        telemetry_charts=telemetry_charts,
        track_map=track_map,
        session_badge=session_badge,
        years=_year_options(),
        events=ctx["event_options"],
        sessions=ctx["session_options"],
        form_action="/data",
        form={
            "year": ctx["year"],
            "gp": ctx["gp"],
            "session": ctx["session_code"],
        },
        drivers=driver_options,
        selected_driver=driver_number,
        selected_driver_data=selected_driver_data,
        selected_lap=lap_value,
    )


@app.route("/strategy")
def strategy():
    ctx = _resolve_context()
    session_state = _get_session_state(ctx["year"], ctx["gp"], ctx["session_code"])
    data = session_state if session_state.get("status") == "ready" else None
    loading = session_state.get("status") == "loading"
    error = session_state.get("message") if session_state.get("status") == "error" else None
    if ctx["schedule_error"] and not error:
        error = ctx["schedule_error"]
    strategy_data = _strategy_summary(data) if data else None
    session_badge = ctx["session_badge"]
    if data:
        session_badge = f"{data['year']} {data['event_name']} - {data['session_name']}"

    return render_template(
        "index.html",
        page="strategy",
        data=data,
        error=error,
        loading=loading,
        view="strategy",
        strategy=strategy_data,
        session_badge=session_badge,
        years=_year_options(),
        events=ctx["event_options"],
        sessions=ctx["session_options"],
        form_action="/strategy",
        form={
            "year": ctx["year"],
            "gp": ctx["gp"],
            "session": ctx["session_code"],
        },
    )


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False, use_reloader=False)
