import os
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import re
from threading import Lock

import fastf1
import pandas as pd
import numpy as np
from flask import Flask, render_template, request, url_for


app = Flask(__name__)
app.config["TEMPLATES_AUTO_RELOAD"] = True
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
TEAM_LOGO_URLS = {
}
TEAM_LOCAL_LOGOS = {
    "Mercedes": "team-logos/mercedes.png",
    "Red Bull Racing": "team-logos/red-bull-racing.svg",
    "RB F1 Team": "team-logos/alphatauri.png",
    "RB": "team-logos/alphatauri.png",
    "Visa Cash App RB": "team-logos/alphatauri.png",
    "Visa Cash App RB F1 Team": "team-logos/alphatauri.png",
    "Visa Cash App RB Formula One Team": "team-logos/alphatauri.png",
    "Ferrari": "team-logos/ferrari.svg",
    "McLaren": "team-logos/mclaren.png",
    "Aston Martin": "team-logos/aston-martin.svg",
    "Alpine": "team-logos/alpine.svg",
    "Williams": "team-logos/williams.png",
    "Haas F1 Team": "team-logos/haas.svg",
    "Alfa Romeo": "team-logos/alfa-romeo.svg",
    "AlphaTauri": "team-logos/alphatauri.png",
    "Sauber": "team-logos/sauber.svg",
    "Kick Sauber": "team-logos/stake-kick-sauber.png",
    "Stake F1 Team Kick Sauber": "team-logos/stake-kick-sauber.png",
    "Mercedes-AMG PETRONAS F1 Team": "team-logos/mercedes.png",
    "Mercedes-AMG Petronas F1 Team": "team-logos/mercedes.png",
    "Mercedes-AMG Petronas Formula One Team": "team-logos/mercedes.png",
    "Mercedes AMG Petronas F1 Team": "team-logos/mercedes.png",
}
TEAM_DEFAULT_LOGO = "team-logos/team-default.svg"
DRIVER_HEADSHOT_DIR = Path(__file__).with_name("static") / "driver-headshots"
TYRE_COMPOUND_COLORS = {
    "SOFT": "#ff4d4d",
    "MEDIUM": "#ffd84d",
    "HARD": "#ffffff",
    "INTERMEDIATE": "#34d399",
    "WET": "#4d9cff",
    "FULL WET": "#4d9cff",
    "EXTREME WET": "#4d9cff",
    "INTERS": "#34d399",
    "C1": "#f3f4f6",
    "C2": "#e5e7eb",
    "C3": "#d1d5db",
    "C4": "#ffd84d",
    "C5": "#ff6b6b",
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


def _format_lap_time(value):
    if pd.isna(value):
        return "-"
    if isinstance(value, pd.Timedelta):
        total_seconds = value.total_seconds()
    else:
        try:
            total_seconds = float(value.total_seconds())
        except (AttributeError, TypeError, ValueError):
            try:
                total_seconds = float(pd.to_timedelta(value).total_seconds())
            except Exception:
                return _clean_value(value)

    minutes, seconds = divmod(total_seconds, 60)
    whole_seconds = int(seconds)
    millis = int(round((seconds - whole_seconds) * 1000))
    if millis == 1000:
        whole_seconds += 1
        millis = 0
    minutes = int(minutes)
    if minutes > 0:
        return f"{minutes}:{whole_seconds:02d}.{millis:03d}"
    return f"{whole_seconds}.{millis:03d}" if millis else f"{whole_seconds}"


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


def _team_logo_url(team_name):
    normalized = _clean_value(team_name)
    if normalized in TEAM_LOCAL_LOGOS:
        return url_for("static", filename=TEAM_LOCAL_LOGOS[normalized])
    lowered = normalized.lower()
    if lowered in {"rb", "rb f1 team", "visa cash app rb", "visa cash app rb f1 team", "visa cash app rb formula one team"}:
        return url_for("static", filename="team-logos/alphatauri.png")
    if "mercedes" in lowered:
        return url_for("static", filename="team-logos/mercedes.png")
    if "red bull" in lowered:
        return url_for("static", filename="team-logos/red-bull-racing.svg")
    if "ferrari" in lowered:
        return url_for("static", filename="team-logos/ferrari.svg")
    if "mclaren" in lowered:
        return url_for("static", filename="team-logos/mclaren.png")
    if "aston" in lowered:
        return url_for("static", filename="team-logos/aston-martin.svg")
    if "alpine" in lowered:
        return url_for("static", filename="team-logos/alpine.svg")
    if "williams" in lowered:
        return url_for("static", filename="team-logos/williams.png")
    if "haas" in lowered:
        return url_for("static", filename="team-logos/haas.svg")
    if "alpha" in lowered:
        return url_for("static", filename="team-logos/alphatauri.png")
    if "stake" in lowered or "kick" in lowered or "sauber" in lowered or "alfa" in lowered:
        return url_for("static", filename="team-logos/stake-kick-sauber.png")
    return url_for("static", filename=TEAM_DEFAULT_LOGO)


def _driver_headshot_url(driver_id, headshot_url=""):
    driver_id = _clean_value(driver_id).strip()
    if driver_id:
        local_name = f"{driver_id}.png"
        local_path = DRIVER_HEADSHOT_DIR / local_name
        if local_path.exists():
            return url_for("static", filename=f"driver-headshots/{local_name}")
    return _clean_value(headshot_url) if headshot_url else url_for("static", filename=TEAM_DEFAULT_LOGO)


def _tyre_color(compound):
    normalized = _clean_value(compound).strip().upper()
    return TYRE_COMPOUND_COLORS.get(normalized, "#94a3b8")


def _lap_time_seconds(value):
    if pd.isna(value):
        return None
    if isinstance(value, pd.Timedelta):
        return float(value.total_seconds())
    if hasattr(value, "total_seconds"):
        try:
            return float(value.total_seconds())
        except (TypeError, ValueError):
            pass
    try:
        return float(value)
    except (TypeError, ValueError):
        try:
            parsed = pd.to_timedelta(value)
        except Exception:
            return None
        if pd.isna(parsed):
            return None
        return float(parsed.total_seconds())


def _driver_options(session, results=None):
    if results is None:
        results = getattr(session, "results", None)
    if results is None or results.empty:
        return []

    columns = [
        col
        for col in ["DriverNumber", "DriverId", "HeadshotUrl", "Abbreviation", "FullName", "TeamName", "Position"]
        if col in results.columns
    ]
    if not columns:
        return []

    ordered = results.loc[:, columns].copy()
    if "Position" in ordered.columns:
        ordered = ordered.sort_values(by="Position")

    options = []
    for _, row in ordered.iterrows():
        driver_number = _clean_value(row.get("DriverNumber", ""))
        driver_id = _clean_value(row.get("DriverId", driver_number))
        abbreviation = _clean_value(row.get("Abbreviation", driver_number))
        full_name = _clean_value(row.get("FullName", abbreviation))
        team_name = _clean_value(row.get("TeamName", "-"))
        headshot_url = _driver_headshot_url(driver_id, row.get("HeadshotUrl", ""))
        team_badge_text, team_badge_color = _team_badge(team_name)
        options.append(
            {
                "value": driver_number,
                "driver_id": driver_id,
                "abbreviation": abbreviation,
                "full_name": full_name,
                "team_name": team_name,
                "team_badge_text": team_badge_text,
                "team_badge_color": team_badge_color,
                "team_logo_url": _team_logo_url(team_name),
                "headshot_url": headshot_url,
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


def _driver_groups(session, results=None):
    drivers = _driver_options(session, results=results)
    if not drivers:
        return []

    grouped = {}
    order = []
    for driver in drivers:
        team_name = driver.get("team_name", "-")
        if team_name not in grouped:
            grouped[team_name] = []
            order.append(team_name)
        grouped[team_name].append(driver)

    return [{"team_name": team_name, "drivers": grouped[team_name]} for team_name in order]


def _qualifying_driver_results(session, phase):
    if not session:
        return None

    results = getattr(session, "results", None)
    if results is None or results.empty:
        return None

    phase_code = str(phase or "Q1").strip().upper()
    if phase_code not in {"Q1", "Q2", "Q3"}:
        phase_code = "Q1"

    if phase_code not in results.columns:
        return results

    ordered = results[results[phase_code].notna()].copy()
    if ordered.empty:
        return ordered

    return ordered.sort_values(by=phase_code, na_position="last")


def _qualifying_phase_rows(session, phase):
    results = _qualifying_driver_results(session, phase)
    if results is None or results.empty:
        return []

    phase_code = str(phase or "Q1").strip().upper()
    if phase_code not in {"Q1", "Q2", "Q3"}:
        phase_code = "Q1"

    rows = []
    for _, row in results.iterrows():
        driver_number = _clean_value(row.get("DriverNumber", ""))
        driver_id = _clean_value(row.get("DriverId", driver_number))
        full_name = _clean_value(row.get("FullName", "-"))
        abbreviation = _clean_value(row.get("Abbreviation", "-"))
        team_name = _clean_value(row.get("TeamName", "-"))
        phase_time = _format_lap_time(row.get(phase_code, None))
        headshot_url = _driver_headshot_url(driver_id, row.get("HeadshotUrl", ""))
        team_badge_text, team_badge_color = _team_badge(team_name)
        rows.append(
            {
                "value": driver_number,
                "driver_id": driver_id,
                "abbreviation": abbreviation,
                "full_name": full_name,
                "team_name": team_name,
                "team_badge_text": team_badge_text,
                "team_badge_color": team_badge_color,
                "headshot_url": headshot_url,
                "phase_time": phase_time,
                "position": _clean_value(row.get("Position", "-")),
                "label": f"{abbreviation} - {full_name}",
            }
        )
    return rows


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
        stint = _clean_value(row.get("Stint", "-"))
        lap_time_text = _format_lap_time(lap_time)
        value = f"{_clean_value(driver_number)}:{_clean_value(lap_number)}"
        label = f"Lap {_clean_value(lap_number)} - {lap_time_text} - {compound}"
        options.append(
            {
                "value": value,
                "lap_number": _clean_value(lap_number),
                "lap_time": lap_time_text,
                "stint": stint,
                "compound": compound,
                "label": label,
            }
        )
    return options


def _qualifying_lap_options(session, driver_number):
    if not session or not driver_number:
        return []

    driver_laps = session.laps.pick_drivers(driver_number)
    if driver_laps is None or driver_laps.empty:
        return []

    valid = driver_laps.copy()
    valid = valid.sort_values(by=["LapNumber", "Time"])

    options = []
    run_number = 0
    for _, row in valid.iterrows():
        lap_number = row.get("LapNumber")
        if pd.isna(lap_number):
            continue

        lap_time = row.get("LapTime")
        compound = _clean_value(row.get("Compound", "-"))
        lap_time_text = _format_lap_time(lap_time)
        pit_out = pd.notna(row.get("PitOutTime", None))
        pit_in = pd.notna(row.get("PitInTime", None))

        if pit_out:
            run_number += 1
        if run_number == 0:
            run_number = 1

        if pit_out and pit_in:
            lap_role = "Out / In Lap"
        elif pit_out:
            lap_role = "Out Lap"
        elif pit_in:
            lap_role = "In Lap"
        else:
            lap_role = "Flying Lap"

        value = f"{_clean_value(driver_number)}:{_clean_value(lap_number)}"
        options.append(
            {
                "value": value,
                "lap_number": _clean_value(lap_number),
                "lap_time": lap_time_text,
                "stint": f"Run {run_number}",
                "compound": compound,
                "lap_type": lap_role,
                "label": f"Lap {_clean_value(lap_number)} - {lap_time_text} - {compound}",
            }
        )

    return options


def _qualifying_run_options(session, driver_number):
    if not session or not driver_number:
        return []

    driver_laps = session.laps.pick_drivers(driver_number)
    if driver_laps is None or driver_laps.empty:
        return []

    valid = driver_laps.copy().sort_values(by=["LapNumber", "Time"])
    runs = []
    current_run = None
    run_number = 0

    def finish_run(run):
        if not run or not run["laps"]:
            return None
        flying_laps = [lap for lap in run["laps"] if lap["lap_type"] == "Flying Lap" and lap["lap_time_seconds"] is not None]
        if flying_laps:
            representative = min(flying_laps, key=lambda lap: lap["lap_time_seconds"])
        else:
            representative = next((lap for lap in run["laps"] if lap["lap_time_seconds"] is not None), run["laps"][0])
        return {
            "run_number": run["run_number"],
            "lap_count": len(run["laps"]),
            "value": representative["value"],
            "lap_number": representative["lap_number"],
            "lap_time": representative["lap_time"],
            "compound": representative["compound"],
            "lap_type": representative["lap_type"],
            "label": f"Run {run['run_number']} - {representative['lap_time']}",
        }

    for _, row in valid.iterrows():
        lap_number = row.get("LapNumber")
        if pd.isna(lap_number):
            continue

        lap_time = row.get("LapTime")
        compound = _clean_value(row.get("Compound", "-"))
        lap_time_text = _format_lap_time(lap_time)
        pit_out = pd.notna(row.get("PitOutTime", None))
        pit_in = pd.notna(row.get("PitInTime", None))

        if pit_out and current_run is not None:
            run_item = finish_run(current_run)
            if run_item is not None:
                runs.append(run_item)
            current_run = None

        if current_run is None:
            run_number += 1
            current_run = {"run_number": run_number, "laps": []}

        if pit_out and pit_in:
            lap_role = "Out / In Lap"
        elif pit_out:
            lap_role = "Out Lap"
        elif pit_in:
            lap_role = "In Lap"
        else:
            lap_role = "Flying Lap"

        current_run["laps"].append(
            {
                "value": f"{_clean_value(driver_number)}:{_clean_value(lap_number)}",
                "lap_number": _clean_value(lap_number),
                "lap_time": lap_time_text,
                "lap_time_seconds": _lap_time_seconds(lap_time),
                "compound": compound,
                "lap_type": lap_role,
            }
        )

    run_item = finish_run(current_run)
    if run_item is not None:
        runs.append(run_item)

    return runs


def _qualifying_run_laps(session, driver_number):
    if not session or not driver_number:
        return []

    driver_laps = session.laps.pick_drivers(driver_number)
    if driver_laps is None or driver_laps.empty:
        return []

    valid = driver_laps.copy().sort_values(by=["LapNumber", "Time"])
    runs = []
    current_run = None
    run_number = 0

    def finish_run(run):
        if not run or not run["laps"]:
            return None
        return {
            "run_number": run["run_number"],
            "laps": run["laps"],
            "flying_time": next(
                (lap["lap_time"] for lap in run["laps"] if lap["lap_type"] == "Flying Lap" and lap["lap_time_seconds"] is not None),
                next((lap["lap_time"] for lap in run["laps"] if lap["lap_time_seconds"] is not None), run["laps"][0]["lap_time"]),
            ),
            "flying_time_seconds": next(
                (lap["lap_time_seconds"] for lap in run["laps"] if lap["lap_type"] == "Flying Lap" and lap["lap_time_seconds"] is not None),
                next((lap["lap_time_seconds"] for lap in run["laps"] if lap["lap_time_seconds"] is not None), None),
            ),
            "representative": next(
                (lap["value"] for lap in run["laps"] if lap["lap_type"] == "Flying Lap" and lap["lap_time_seconds"] is not None),
                next((lap["value"] for lap in run["laps"] if lap["lap_time_seconds"] is not None), run["laps"][0]["value"]),
            ),
        }

    for _, row in valid.iterrows():
        lap_number = row.get("LapNumber")
        if pd.isna(lap_number):
            continue

        lap_time = row.get("LapTime")
        compound = _clean_value(row.get("Compound", "-"))
        lap_time_text = _format_lap_time(lap_time)
        lap_time_seconds = _lap_time_seconds(lap_time)
        pit_out = pd.notna(row.get("PitOutTime", None))
        pit_in = pd.notna(row.get("PitInTime", None))

        if pit_out and current_run is not None:
            run_item = finish_run(current_run)
            if run_item is not None:
                runs.append(run_item)
            current_run = None

        if current_run is None:
            run_number += 1
            current_run = {"run_number": run_number, "laps": []}

        if pit_out and pit_in:
            lap_role = "Out / In Lap"
        elif pit_out:
            lap_role = "Out Lap"
        elif pit_in:
            lap_role = "In Lap"
        else:
            lap_role = "Flying Lap"

        current_run["laps"].append(
            {
                "value": f"{_clean_value(driver_number)}:{_clean_value(lap_number)}",
                "lap_number": _clean_value(lap_number),
                "lap_time": lap_time_text,
                "lap_time_seconds": lap_time_seconds,
                "compound": compound,
                "lap_type": lap_role,
                "is_flying": lap_role == "Flying Lap",
            }
        )

    run_item = finish_run(current_run)
    if run_item is not None:
        runs.append(run_item)

    flying_times = [run["flying_time_seconds"] for run in runs if run.get("flying_time_seconds") is not None]
    fastest_flying_time = min(flying_times) if flying_times else None
    for run in runs:
        run["is_fastest"] = fastest_flying_time is not None and run.get("flying_time_seconds") == fastest_flying_time

    return runs


def _qualifying_driver_results(session, phase):
    if not session:
        return None

    results = getattr(session, "results", None)
    if results is None or results.empty:
        return None

    phase_code = str(phase or "Q1").strip().upper()
    if phase_code not in {"Q1", "Q2", "Q3"}:
        phase_code = "Q1"

    if phase_code not in results.columns:
        return results

    ordered = results[results[phase_code].notna()].copy()
    if ordered.empty:
        return ordered

    ordered = ordered.sort_values(by=phase_code, na_position="last")
    return ordered


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


def _resolve_driver(session, driver_number, drivers=None):
    if drivers is None:
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
        "lap_time": _format_lap_time(lap.get("LapTime", "-")),
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
        ("stint", "Stint", _clean_value(lap.get("Stint", "-"))),
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
            lap_time_seconds = _lap_time_seconds(lap_time)
            laps.append(
                {
                    "lap_number": lap_number_int,
                    "lap_time": _clean_value(lap_time),
                    "lap_time_seconds": lap_time_seconds,
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
                "tyre_color": _tyre_color(compound),
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


def _qualifying_phases(session, driver_number, session_code):
    if not session or not driver_number or str(session_code).strip().upper() != "Q":
        return None

    results = getattr(session, "results", None)
    if results is None or results.empty:
        return None

    row_match = results[results["DriverNumber"].astype(str).str.strip() == str(driver_number).strip()]
    if row_match.empty:
        return None

    row = row_match.iloc[0]
    driver_laps = session.laps.pick_drivers(driver_number)
    if driver_laps is None or driver_laps.empty:
        return None

    valid_laps = driver_laps[driver_laps["LapTime"].notna()].copy()
    if valid_laps.empty:
        return None

    phase_specs = []
    for phase_number, field_name in enumerate(("Q1", "Q2", "Q3"), start=1):
        phase_time = row.get(field_name, None)
        phase_seconds = _lap_time_seconds(phase_time)
        if phase_seconds is None:
            continue
        phase_specs.append(
            {
                "stint": phase_number,
                "phase_label": field_name,
                "phase_time": phase_time,
                "phase_seconds": phase_seconds,
                "tyre_color": "#44c2ff" if phase_number == 1 else "#77f0d1" if phase_number == 2 else "#ffbf69",
            }
        )

    if not phase_specs:
        return None

    phase_laps = {spec["stint"]: [] for spec in phase_specs}
    ordered_laps = valid_laps.sort_values(by=["LapNumber", "Time"])
    for _, lap_row in ordered_laps.iterrows():
        lap_number = lap_row.get("LapNumber")
        if pd.isna(lap_number):
            continue
        lap_seconds = _lap_time_seconds(lap_row.get("LapTime", None))
        if lap_seconds is None:
            continue

        best_phase = min(
            phase_specs,
            key=lambda spec: abs(lap_seconds - spec["phase_seconds"]),
        )
        lap_number_int = int(lap_number)
        phase_laps[best_phase["stint"]].append(
            {
                "lap_number": lap_number_int,
                "lap_time": _clean_value(lap_row.get("LapTime", "-")),
                "lap_time_seconds": lap_seconds,
                "value": f"{_clean_value(driver_number)}:{lap_number_int}",
                "compound": _clean_value(lap_row.get("Compound", "-")),
                "fresh_tyre": bool(lap_row.get("FreshTyre", False)) if pd.notna(lap_row.get("FreshTyre", False)) else False,
            }
        )

    phases = []
    for spec in phase_specs:
        laps = phase_laps.get(spec["stint"], [])
        phases.append(
            {
                "stint": spec["stint"],
                "phase_label": spec["phase_label"],
                "phase_time": _format_lap_time(spec["phase_time"]),
                "lap_count": len(laps),
                "compound": "-",
                "tyre_color": spec["tyre_color"],
                "fresh_tyre": False,
                "laps": laps,
            }
        )

    if not phases:
        return None

    return {
        "driver": _clean_value(row.get("FullName", "-")),
        "stints": phases,
        "lap_total": int(valid_laps.shape[0]),
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

        max_point = None
        min_point = None
        if column == "Speed":
            speed_series = telemetry[column]
            if not speed_series.empty:
                for label_name, selector in (("max", speed_series.idxmax), ("min", speed_series.idxmin)):
                    peak_index = selector()
                    if peak_index not in telemetry.index:
                        continue
                    peak_row = telemetry.loc[peak_index]
                    peak_time = peak_row.get("Time", None)
                    peak_speed = peak_row.get("Speed", None)
                    if pd.isna(peak_time) or pd.isna(peak_speed):
                        continue
                    try:
                        marker = {
                            "time": float(peak_time.total_seconds()),
                            "value": float(peak_speed),
                            "label": f"{float(peak_speed):.0f} km/h",
                        }
                    except Exception:
                        continue
                    if label_name == "max":
                        max_point = marker
                    else:
                        min_point = marker

        y_values = [point[1] for point in points]
        if column == "nGear":
            y_min = -1
            y_max = 8
            y_ticks = list(range(-1, 9))
            y_tick_labels = ["R", "N", "1", "2", "3", "4", "5", "6", "7", "8"]
        elif column == "Speed":
            y_min = 0
            y_max = max(y_values)
            if y_max == y_min:
                y_max += 1
            y_ticks = None
            y_tick_labels = None
        else:
            y_min = min(y_values)
            y_max = max(y_values)
            if y_min == y_max:
                y_min -= 1
                y_max += 1
            y_ticks = None
            y_tick_labels = None

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
                "y_ticks": y_ticks,
                "y_tick_labels": y_tick_labels,
                "max_point": max_point,
                "min_point": min_point,
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


def _sample_track_point(samples, target_seconds):
    if not samples:
        return None
    if len(samples) == 1:
        return samples[0]

    if target_seconds <= samples[0]["t"]:
        return samples[0]
    if target_seconds >= samples[-1]["t"]:
        return samples[-1]

    for index in range(1, len(samples)):
        prev = samples[index - 1]
        next_sample = samples[index]
        if target_seconds > next_sample["t"]:
            continue

        span = next_sample["t"] - prev["t"] or 1.0
        ratio = (target_seconds - prev["t"]) / span
        return {
            "x": prev["x"] + (next_sample["x"] - prev["x"]) * ratio,
            "y": prev["y"] + (next_sample["y"] - prev["y"]) * ratio,
        }

    return samples[-1]


def _track_map_payload(session, lap, lap_duration_seconds=None, telemetry=None):
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
    racing_frame = lap_pos.loc[:, [column for column in ["Time", "X", "Y"] if column in lap_pos.columns]].dropna(subset=["X", "Y"]).copy()
    racing = racing_frame.loc[:, ["X", "Y"]].to_numpy().tolist()
    outline = _rotate_xy(outline, circuit_info.rotation)
    racing = _rotate_xy(racing, circuit_info.rotation)

    if "Time" in racing_frame.columns:
        time_values = racing_frame["Time"].dt.total_seconds().tolist()
        time_base = next((value for value in time_values if pd.notna(value)), 0.0)
        raw_time_series = [
            float(value - time_base) if pd.notna(value) else index / max(len(racing) - 1, 1)
            for index, value in enumerate(time_values)
        ]
    else:
        raw_time_series = [index / max(len(racing) - 1, 1) for index in range(len(racing))]

    if lap_duration_seconds is not None and raw_time_series:
        source_duration = raw_time_series[-1] or 1.0
        scale = float(lap_duration_seconds) / source_duration
    else:
        scale = 1.0

    time_series = [float(value * scale) for value in raw_time_series]

    racing_samples = []
    for index, point in enumerate(racing):
        if index >= len(time_series):
            break
        racing_samples.append(
            {
                "x": float(point[0]),
                "y": float(point[1]),
                "t": float(time_series[index]),
            }
        )

    max_speed_marker = None
    min_speed_marker = None
    if telemetry is not None and not telemetry.empty and "Speed" in telemetry.columns:
        speed_series = telemetry["Speed"].dropna()
        if not speed_series.empty:
            for marker_name, selector in (("max", speed_series.idxmax), ("min", speed_series.idxmin)):
                speed_index = selector()
                if speed_index not in telemetry.index:
                    continue
                speed_row = telemetry.loc[speed_index]
                speed_time = speed_row.get("Time", None)
                if pd.isna(speed_time):
                    continue
                target_seconds = _lap_time_seconds(speed_time)
                if target_seconds is None:
                    continue
                point = _sample_track_point(racing_samples, target_seconds)
                if point is None:
                    continue
                marker = {
                    "speed": float(speed_row.get("Speed", 0.0)),
                    "seconds": float(target_seconds),
                    "x": float(point["x"]),
                    "y": float(point["y"]),
                }
                if marker_name == "max":
                    max_speed_marker = marker
                else:
                    min_speed_marker = marker

    sector_markers = []
    sector_specs = [
        ("S1", lap.get("Sector1Time", None)),
        ("S2", lap.get("Sector2Time", None)),
        ("S3", lap.get("Sector3Time", None)),
    ]
    cumulative_seconds = 0.0
    for label, sector_value in sector_specs:
        sector_seconds = _lap_time_seconds(sector_value)
        if sector_seconds is None:
            continue
        cumulative_seconds += sector_seconds
        point = _sample_track_point(racing_samples, cumulative_seconds)
        if point is None:
            continue
        sector_markers.append(
            {
                "label": label,
                "time": _clean_value(sector_value),
                "seconds": float(sector_seconds),
                "cumulative_seconds": float(cumulative_seconds),
                "x": float(point["x"]),
                "y": float(point["y"]),
            }
        )

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
        "samples": racing_samples,
        "sector_markers": sector_markers,
        "max_speed_marker": max_speed_marker,
        "min_speed_marker": min_speed_marker,
        "bounds": {
            "min_x": min(xs) - padding,
            "max_x": max(xs) + padding,
            "min_y": min(ys) - padding,
            "max_y": max(ys) + padding,
        },
        "rotation": circuit_info.rotation,
        "duration": float(lap_duration_seconds) if lap_duration_seconds is not None else None,
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

    session_title = f"{year} {gp}"
    if selected_event is not None:
        session_title = f"{year} {selected_event.EventName}"

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
        "session_title": session_title,
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
        session_title=ctx["session_title"],
        years=_year_options(),
        events=ctx["event_options"],
        session_options=ctx["session_options"],
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
        session_title=ctx["session_title"],
        years=_year_options(),
        events=ctx["event_options"],
        session_options=ctx["session_options"],
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
    driver_number = request.args.get("driver", "").strip()
    driver_requested = bool(driver_number)
    lap_key = request.args.get("lap", "")
    stint_key = request.args.get("stint", "")
    qualifying_phase = request.args.get("phase", "").strip().upper()
    lap_requested = bool(lap_key)
    session_is_qualifying = str(ctx["session_code"]).strip().upper() == "Q"
    if session_is_qualifying:
        qualifying_phase = qualifying_phase if qualifying_phase in {"Q1", "Q2", "Q3"} else "Q1"
    else:
        qualifying_phase = ""

    qualifying_results = _qualifying_driver_results(session, qualifying_phase) if session_is_qualifying else None
    qualifying_phase_rows = _qualifying_phase_rows(session, qualifying_phase) if session_is_qualifying else []
    driver_options = _driver_options(session, results=qualifying_results) if session else []
    driver_groups = _driver_groups(session, results=qualifying_results) if session else []
    driver_number = _resolve_driver(session, driver_number, driver_options) if session and driver_requested else None
    selected_driver_data = next((option for option in driver_options if option["value"] == driver_number), None)
    qualifying_run_options = _qualifying_run_options(session, driver_number) if session_is_qualifying and session and driver_number else []
    qualifying_run_laps = _qualifying_run_laps(session, driver_number) if session_is_qualifying and session and driver_number else []
    driver_lap_options = _lap_options(session, driver_number) if session and driver_number else []
    race_stints = None
    if session and driver_number:
        race_stints = _qualifying_phases(session, driver_number, ctx["session_code"]) if session_is_qualifying else _race_stints(session, driver_number, ctx["session_code"])
    selected_stint = _parse_stint_value(stint_key)
    selected_lap_value = lap_key if lap_requested else ""
    selected_lap_data = None
    if session and driver_number:
        if lap_requested:
            selected_lap_data = _resolve_lap(session, driver_number, lap_key)
            if selected_lap_data is not None:
                selected_lap_value = f"{_clean_value(driver_number)}:{_clean_value(selected_lap_data.get('LapNumber', ''))}"
                if selected_stint is None:
                    selected_stint = _parse_stint_value(selected_lap_data.get("Stint", None))
    selected_stint_data = None
    if race_stints and race_stints.get("stints") and selected_stint is not None:
        selected_stint_data = next((item for item in race_stints["stints"] if item["stint"] == selected_stint), None)
    selected_qualifying_run = None
    if session_is_qualifying and qualifying_run_laps and lap_requested:
        selected_qualifying_run = next((run for run in qualifying_run_laps if any(lap["value"] == selected_lap_value for lap in run["laps"])), None)

    telemetry_columns = []
    telemetry_rows = []
    telemetry_summary = None
    lap_record = []
    telemetry_charts = []
    track_map = None
    if selected_lap_data is not None:
        telemetry_columns, telemetry_rows, telemetry = _telemetry_rows(selected_lap_data)
        telemetry_summary = _lap_summary(selected_lap_data, telemetry)
        lap_record = _lap_record(selected_lap_data, telemetry)
        telemetry_charts = _telemetry_charts(selected_lap_data)
        try:
            lap_duration = _lap_time_seconds(selected_lap_data.get("LapTime", None))
            track_map = _track_map_payload(session, selected_lap_data, lap_duration, telemetry) if session else None
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
        session_title=ctx["session_title"],
        years=_year_options(),
        events=ctx["event_options"],
        session_options=ctx["session_options"],
        qualifying_phase=qualifying_phase,
        qualifying_phase_rows=qualifying_phase_rows,
        qualifying_run_options=qualifying_run_options,
        qualifying_run_laps=qualifying_run_laps,
        selected_qualifying_run=selected_qualifying_run,
        form_action="/data",
        form={
            "year": ctx["year"],
            "gp": ctx["gp"],
            "session": ctx["session_code"],
            "phase": qualifying_phase,
        },
        drivers=driver_options,
        driver_groups=driver_groups,
        selected_driver=driver_number,
        selected_driver_data=selected_driver_data,
        driver_lap_options=driver_lap_options,
        selected_lap=selected_lap_value,
        session_is_qualifying=session_is_qualifying,
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
        session_title=ctx["session_title"],
        years=_year_options(),
        events=ctx["event_options"],
        session_options=ctx["session_options"],
        form_action="/strategy",
        form={
            "year": ctx["year"],
            "gp": ctx["gp"],
            "session": ctx["session_code"],
        },
    )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5001"))
    app.run(host="127.0.0.1", port=port, debug=False, use_reloader=False)
