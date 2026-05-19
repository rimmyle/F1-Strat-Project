import os
import time
from datetime import datetime
import re
from pathlib import Path
from threading import Event, Lock, Thread

import fastf1
import pandas as pd
import numpy as np
from flask import Flask, jsonify, redirect, render_template, request, url_for


app = Flask(__name__)
app.config["TEMPLATES_AUTO_RELOAD"] = True


@app.after_request
def _disable_html_cache(response):
    if response.mimetype == "text/html":
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


FASTF1_CACHE_DIR = Path(__file__).with_name(".fastf1-cache")
try:
    fastf1.Cache.enable_cache(str(FASTF1_CACHE_DIR))
except Exception:
    pass
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
_SESSION_LOAD_LOCK = Lock()
_SESSION_LOAD_STATE = {}
_SESSION_LOAD_ACTIVE_EVENT = None
_SESSION_LOAD_STAGES = [
    (5, "Resolving event"),
    (15, "Preparing session"),
    (30, "Loading timing data"),
    (50, "Loading laps and results"),
    (72, "Building charts"),
    (88, "Finalizing session data"),
]
_SESSION_LOAD_TIMEOUT_SECONDS = 120


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


def _is_truthy_value(value):
    if pd.isna(value):
        return False
    if isinstance(value, str):
        normalized = value.strip().lower()
        return normalized in {"1", "true", "t", "yes", "y", "deleted"}
    return bool(value)


def _country_flag_emoji(country_code):
    code = str(country_code or "").strip().upper()
    if not code:
        return ""
    alias = {
        "UK": "GB",
        "USA": "US",
    }.get(code, code)
    if len(alias) != 2 or not alias.isalpha():
        return ""
    base = 127397
    return "".join(chr(base + ord(char)) for char in alias)


_DRIVER_COUNTRY_CODES = {
    "max_verstappen": "NL",
    "lewis_hamilton": "GB",
    "charles_leclerc": "MC",
    "carlos_sainz": "ES",
    "lando_norris": "GB",
    "oscar_piastri": "AU",
    "george_russell": "GB",
    "sergio_perez": "MX",
    "fernando_alonso": "ES",
    "lance_stroll": "CA",
    "alex_albon": "TH",
    "pierre_gasly": "FR",
    "esteban_ocon": "FR",
    "yuki_tsunoda": "JP",
    "nico_hulkenberg": "DE",
    "valtteri_bottas": "FI",
    "zhou_guanyu": "CN",
    "kevin_magnussen": "DK",
    "daniel_ricciardo": "AU",
    "carlos_sainz_jr": "ES",
    "nico_rosberg": "DE",
    "sebastian_vettel": "DE",
    "kimi_raikkonen": "FI",
    "jenson_button": "GB",
    "mick_schumacher": "DE",
    "logan_sargeant": "US",
    "oliver_bearman": "GB",
    "franco_colapinto": "AR",
    "liam_lawson": "NZ",
    "jack_doohan": "AU",
    "isack_hadjar": "FR",
    "gabriel_bortoleto": "BR",
    "mercerdes": "GB",
}


def _driver_country_code(row):
    country_code = str(row.get("CountryCode", "") or "").strip().upper()
    if country_code:
        return country_code

    driver_id = _clean_value(row.get("DriverId", "")).strip().lower()
    if driver_id in _DRIVER_COUNTRY_CODES:
        return _DRIVER_COUNTRY_CODES[driver_id]

    abbreviation = _clean_value(row.get("Abbreviation", "")).strip().upper()
    abbrev_to_code = {
        "VER": "NL",
        "HAM": "GB",
        "LEC": "MC",
        "SAI": "ES",
        "NOR": "GB",
        "PIA": "AU",
        "RUS": "GB",
        "PER": "MX",
        "ALO": "ES",
        "STR": "CA",
        "ALB": "TH",
        "GAS": "FR",
        "OCO": "FR",
        "TSU": "JP",
        "HUL": "DE",
        "BOT": "FI",
        "ZHO": "CN",
        "MAG": "DK",
        "RIC": "AU",
        "SAR": "US",
        "BEA": "GB",
        "COL": "AR",
        "LAW": "NZ",
        "DOO": "AU",
        "HAD": "FR",
        "BOR": "BR",
    }
    return abbrev_to_code.get(abbreviation, "")


def _render_error_page(status_code, title, message):
    return render_template(
        "error.html",
        status_code=status_code,
        title=title,
        message=message,
        session_badge="Strategy Predictor",
    ), status_code


def _format_lap_time(value):
    if pd.isna(value):
        return "-"
    if isinstance(value, pd.Timedelta):
        total_seconds = value.total_seconds()
    else:
        try:
            total_seconds = float(value)
        except (AttributeError, TypeError, ValueError):
            try:
                total_seconds = float(value.total_seconds())
            except Exception:
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


def _format_minutes_seconds(value):
    if pd.isna(value):
        return "-"
    if isinstance(value, pd.Timedelta):
        total_seconds = value.total_seconds()
    else:
        try:
            total_seconds = float(value)
        except (AttributeError, TypeError, ValueError):
            try:
                total_seconds = float(value.total_seconds())
            except Exception:
                try:
                    total_seconds = float(pd.to_timedelta(value).total_seconds())
                except Exception:
                    return _clean_value(value)

    total_milliseconds = max(0, int(round(float(total_seconds) * 1000)))
    minutes, remainder = divmod(total_milliseconds, 60000)
    seconds, millis = divmod(remainder, 1000)
    if minutes > 0:
        return f"{minutes}:{seconds:02d}.{millis:03d}"
    return f"{seconds}.{millis:03d}" if millis else f"{seconds}"


def _format_sector_time(value):
    if pd.isna(value):
        return "-"

    if isinstance(value, pd.Timedelta):
        total_seconds = value.total_seconds()
    else:
        try:
            total_seconds = float(value)
        except (AttributeError, TypeError, ValueError):
            try:
                total_seconds = float(value.total_seconds())
            except Exception:
                try:
                    total_seconds = float(pd.to_timedelta(value).total_seconds())
                except Exception:
                    return _clean_value(value)

    total_milliseconds = max(0, int(round(float(total_seconds) * 1000)))
    minutes, remainder = divmod(total_milliseconds, 60000)
    seconds, millis = divmod(remainder, 1000)
    if minutes > 0:
        return f"{minutes}:{seconds:02d}.{millis:03d}"
    return f"{seconds}.{millis:03d}" if millis else f"{seconds}"


def _format_signed_duration(value):
    if value is None:
        return "-"
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        try:
            numeric = float(value.total_seconds())
        except (AttributeError, TypeError, ValueError):
            try:
                numeric = float(pd.to_timedelta(value).total_seconds())
            except Exception:
                return _clean_value(value)

    formatted = _format_minutes_seconds(abs(numeric))
    if numeric > 0:
        return f"+{formatted}"
    if numeric < 0:
        return f"-{formatted}"
    return formatted


def _format_gap_seconds(value):
    if value is None or pd.isna(value):
        return "-"

    try:
        numeric = float(value)
    except (TypeError, ValueError):
        try:
            numeric = float(value.total_seconds())
        except (AttributeError, TypeError, ValueError):
            try:
                numeric = float(pd.to_timedelta(value).total_seconds())
            except Exception:
                return _clean_value(value)

    total_milliseconds = max(0, int(round(float(numeric) * 1000)))
    seconds, millis = divmod(total_milliseconds, 1000)
    if seconds >= 60:
        minutes, remainder = divmod(seconds, 60)
        return f"{minutes}:{remainder:02d}.{millis:03d} s"
    return f"{seconds}.{millis:03d} s" if millis else f"{seconds} s"


def _gap_ahead_seconds(distance_value, speed_value):
    if distance_value is None or speed_value is None:
        return None

    try:
        distance_m = float(distance_value)
        speed_kmh = float(speed_value)
    except (TypeError, ValueError):
        return None

    if not np.isfinite(distance_m) or not np.isfinite(speed_kmh) or speed_kmh <= 0:
        return None

    return max(distance_m, 0.0) / (speed_kmh / 3.6)


def _format_track_status(value):
    status = _clean_value(value).strip()
    if not status or status == "-":
        return "-"

    code_labels = {
        "1": "Green",
        "2": "Yellow Flag",
        "4": "Safety Car",
        "5": "Red Flag",
        "6": "Virtual Safety Car",
        "7": "Virtual Safety Car",
    }
    labels = []
    seen = set()
    for code in str(status):
        label = code_labels.get(code)
        if label and label not in seen:
            labels.append(label)
            seen.add(label)

    return ", ".join(labels) if labels else status


def _format_race_result_time(value, position=None):
    if value in (None, "", "-"):
        return "-"

    try:
        duration = pd.to_timedelta(value)
    except Exception:
        return _clean_value(value)

    total_seconds = float(duration.total_seconds())
    sign = "+" if str(position).strip() != "1" else ""
    hours = int(total_seconds // 3600)
    minutes = int((total_seconds % 3600) // 60)
    seconds = total_seconds % 60
    whole_seconds = int(seconds)
    millis = int(round((seconds - whole_seconds) * 1000))
    if millis == 1000:
        whole_seconds += 1
        millis = 0
        if whole_seconds == 60:
            minutes += 1
            whole_seconds = 0
        if minutes == 60:
            hours += 1
            minutes = 0

    if hours > 0:
        return f"{sign}{hours}:{minutes:02d}:{whole_seconds:02d}.{millis:03d}"
    if minutes > 0:
        return f"{sign}{minutes}:{whole_seconds:02d}.{millis:03d}"
    return f"{sign}{whole_seconds}.{millis:03d}"


def _session_rows(session):
    results = getattr(session, "results", None)
    if results is None or results.empty:
        return []

    preferred_columns = [
        "Position",
        "DriverNumber",
        "Abbreviation",
        "FullName",
        "TeamName",
        "CountryCode",
        "Q1",
        "Q2",
        "Q3",
        "Time",
        "Status",
        "Points",
        "Laps",
    ]
    columns = [column for column in preferred_columns if column in results.columns]
    rows = results.loc[:, columns].head(20).copy()
    output = []
    for _, row in rows.iterrows():
        item = {column: _clean_value(row[column]) for column in columns}
        item["CountryFlag"] = _country_flag_emoji(_driver_country_code(row))
        output.append(item)
    return output


def _session_label(session_code):
    return SESSION_LABELS.get(str(session_code).strip().upper(), str(session_code))


def _session_code_from_name(session_name):
    normalized = _clean_value(session_name).strip().lower()
    practice_match = re.fullmatch(r"(?:free\s+)?practice\s+([123])", normalized)
    if practice_match:
        return f"FP{practice_match.group(1)}"
    reverse_lookup = {label.lower(): code for code, label in SESSION_CHOICES}
    return reverse_lookup.get(normalized)


def _session_selection_label(session_code):
    session_code = str(session_code).strip().upper()
    if not session_code:
        return "No sessions available yet"
    return _session_label(session_code)


def _supports_pit_strategy_graph(session_code):
    session_code = str(session_code).strip().upper()
    return session_code in {"R", "S"} or session_code.startswith("FP")


def _supports_race_position_markers(session_code):
    session_code = str(session_code).strip().upper()
    return session_code in {"R", "S"}


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


def _team_color(team_name):
    normalized = _clean_value(team_name)
    _, badge_color = TEAM_BADGE_STYLES.get(normalized, (None, None))
    if badge_color:
        return badge_color

    lowered = normalized.lower()
    if lowered in {"rb", "rb f1 team", "visa cash app rb", "visa cash app rb f1 team", "visa cash app rb formula one team"}:
        return "#2b4562"
    if "mercedes" in lowered:
        return "#00d2be"
    if "red bull" in lowered:
        return "#1e41ff"
    if "ferrari" in lowered:
        return "#dc0000"
    if "mclaren" in lowered:
        return "#ff8700"
    if "aston" in lowered:
        return "#006f62"
    if "alpine" in lowered:
        return "#0090ff"
    if "williams" in lowered:
        return "#00a3e0"
    if "haas" in lowered:
        return "#b6babd"
    if "alpha" in lowered:
        return "#2b4562"
    if "stake" in lowered or "kick" in lowered or "sauber" in lowered or "alfa" in lowered:
        return "#52e252"
    return "#64748b"


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
            return f"/static/driver-headshots/{local_name}"
    return _clean_value(headshot_url) if headshot_url else f"/static/{TEAM_DEFAULT_LOGO}"


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
        return _driver_options_from_laps(session)

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
        result_time = _format_race_result_time(row.get("Time", "-"), row.get("Position", "-"))
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
                "result_time": result_time,
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


def _driver_options_from_laps(session):
    laps = _safe_session_laps(session)
    if laps is None or laps.empty:
        return []

    driver_key_column = None
    if "DriverNumber" in laps.columns:
        driver_key_column = "DriverNumber"
    elif "Driver" in laps.columns:
        driver_key_column = "Driver"
    if driver_key_column is None:
        return []

    working = laps.copy()
    if driver_key_column == "DriverNumber":
        working["_driver_key"] = working["DriverNumber"].map(_normalize_driver_number)
    else:
        working["_driver_key"] = working["Driver"].astype(str).str.strip()
    working = working[working["_driver_key"].astype(str).str.strip() != ""].copy()
    if working.empty:
        return []

    options = []
    for driver_key, driver_laps in working.groupby("_driver_key", dropna=True, sort=False):
        if driver_laps.empty:
            continue

        first_row = driver_laps.iloc[0]
        driver_number = _normalize_driver_number(first_row.get("DriverNumber", driver_key)) or _clean_value(driver_key).strip()
        abbreviation = _clean_value(first_row.get("Abbreviation", first_row.get("Driver", driver_number))).strip()
        full_name = _clean_value(first_row.get("FullName", abbreviation or driver_number)).strip()
        if not full_name or full_name == "-":
            full_name = abbreviation or driver_number or _clean_value(driver_key).strip()
        team_name = _clean_value(first_row.get("TeamName", first_row.get("Team", "-"))).strip() or "-"
        driver_id = _clean_value(first_row.get("DriverId", abbreviation or driver_number)).strip() or driver_number
        headshot_url = _driver_headshot_url(driver_id, first_row.get("HeadshotUrl", ""))
        team_badge_text, team_badge_color = _team_badge(team_name)

        best_lap_seconds = None
        best_lap_value = None
        if "LapTime" in driver_laps.columns:
            for lap_time in driver_laps["LapTime"].tolist():
                lap_seconds = _lap_time_seconds(lap_time)
                if lap_seconds is None:
                    continue
                if best_lap_seconds is None or lap_seconds < best_lap_seconds:
                    best_lap_seconds = lap_seconds
                    best_lap_value = lap_time

        options.append(
            {
                "value": driver_number or driver_id or abbreviation or _clean_value(driver_key).strip(),
                "driver_id": driver_id,
                "abbreviation": abbreviation or driver_number or _clean_value(driver_key).strip(),
                "full_name": full_name or abbreviation or driver_number or _clean_value(driver_key).strip(),
                "team_name": team_name,
                "team_badge_text": team_badge_text,
                "team_badge_color": team_badge_color,
                "team_logo_url": _team_logo_url(team_name),
                "headshot_url": headshot_url,
                "label": f"{abbreviation} - {full_name}" if abbreviation and full_name else full_name or abbreviation or driver_number,
                "result_time": _format_lap_time(best_lap_value) if best_lap_value is not None else "-",
                "_best_lap_seconds": best_lap_seconds if best_lap_seconds is not None else float("inf"),
            }
        )

    options.sort(key=lambda option: (option.get("_best_lap_seconds", float("inf")), option.get("abbreviation", ""), option.get("full_name", ""), option.get("value", "")))
    for position, option in enumerate(options, start=1):
        option["position"] = position
        option.pop("_best_lap_seconds", None)

    return options


def _driver_abbreviation_lookup(session, results=None):
    if results is None:
        results = getattr(session, "results", None)
    if results is None or results.empty:
        return {}
    if "DriverNumber" not in results.columns or "Abbreviation" not in results.columns:
        return {}

    lookup = {}
    for _, row in results.iterrows():
        driver_number = _normalize_driver_number(row.get("DriverNumber", ""))
        abbreviation = _clean_value(row.get("Abbreviation", "")).strip()
        if driver_number and abbreviation:
            lookup[driver_number] = abbreviation
    return lookup


def _pit_strategy_driver_options(pit_strategy_graph):
    if not pit_strategy_graph:
        return []

    drivers = pit_strategy_graph.get("drivers", [])
    if not drivers:
        return []

    options = []
    for driver in drivers:
        driver_number = _clean_value(driver.get("driver_number", "")).strip()
        abbreviation = _clean_value(driver.get("abbreviation", "")).strip()
        full_name = _clean_value(driver.get("driver", "")).strip()
        team_name = _clean_value(driver.get("team", "-")).strip()
        driver_id = _clean_value(driver.get("driver_id", driver_number)).strip() or driver_number
        headshot_url = _driver_headshot_url(driver_id, driver.get("headshot_url", ""))
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
                "label": f"{abbreviation} - {full_name}" if abbreviation and full_name else full_name or abbreviation or driver_number,
                "result_time": _clean_value(driver.get("time_label", "-")).strip() or "-",
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

    ordered = results.copy() if phase_code == "Q1" else results[results[phase_code].notna()].copy()
    return ordered.sort_values(by=phase_code, na_position="last")


def _qualifying_driver_key(row):
    for column in ("DriverNumber", "Abbreviation", "Driver", "FullName", "DriverId"):
        value = _clean_value(row.get(column, "")).strip()
        if value and value != "-":
            return value
    return ""


def _qualifying_driver_rows(session):
    results = getattr(session, "results", None)
    if results is None or results.empty:
        return None

    combined = results.copy()
    combined["_DriverKey"] = combined.apply(_qualifying_driver_key, axis=1)
    combined = combined[combined["_DriverKey"].astype(str).str.strip() != ""].copy()
    combined = combined.drop_duplicates(subset=["_DriverKey"], keep="first")
    return combined


def _qualifying_finalize_run_laps(run_laps):
    if not run_laps:
        return

    flying_laps = [
        lap
        for lap in run_laps
        if lap.get("lap_type") == "Flying Lap" and lap.get("lap_time_seconds") is not None
    ]
    flying_seconds = [lap["lap_time_seconds"] for lap in flying_laps if lap.get("lap_time_seconds") is not None]
    baseline_flying = min(flying_seconds) if flying_seconds else None
    if baseline_flying is None:
        return

    timed_laps = [
        lap
        for lap in run_laps
        if lap.get("lap_time_seconds") is not None
        and lap.get("lap_type") not in {"Out Lap", "In Lap", "Out / In Lap"}
    ]
    if len(timed_laps) <= 1:
        return

    cool_threshold = max(baseline_flying * 1.02, baseline_flying + 1.0)
    for lap in timed_laps:
        lap_time_seconds = lap.get("lap_time_seconds")
        if lap_time_seconds is not None and lap_time_seconds > cool_threshold:
            lap["lap_type"] = "Cool Lap"
        else:
            lap["lap_type"] = "Flying Lap"


def _qualifying_phase_windows(session):
    phase_windows = {}
    if not session:
        return phase_windows

    event = getattr(session, "event", None)
    session_code = "SQ" if "sprint" in str(getattr(session, "name", "")).strip().lower() else "Q"
    session_start = None
    if event is not None:
        if session_code == "SQ":
            session_start = getattr(event, "Session2Date", None)
        else:
            session_start = getattr(event, "Session4Date", None)
    if session_start is None:
        session_start = getattr(session, "date", None)
    if session_start is None:
        return phase_windows

    try:
        messages = getattr(session, "race_control_messages", None)
    except Exception:
        messages = None
    if messages is None or messages.empty or "Message" not in messages.columns:
        return phase_windows

    pattern = re.compile(r"\b(Q[23])\s+WILL\s+START\s+AT\s+(\d{1,2}):(\d{2})\b", re.IGNORECASE)
    session_start_ts = pd.Timestamp(session_start)
    for message in messages["Message"].astype(str):
        match = pattern.search(message)
        if not match:
            continue
        phase_code = match.group(1).upper()
        hour = int(match.group(2))
        minute = int(match.group(3))
        try:
            phase_start_ts = session_start_ts.replace(hour=hour, minute=minute, second=0, microsecond=0)
        except Exception:
            continue
        if phase_start_ts < session_start_ts:
            phase_start_ts += pd.Timedelta(days=1)
        phase_windows[phase_code] = max(0.0, float((phase_start_ts - session_start_ts).total_seconds()))

    return phase_windows


def _qualifying_phase_durations(session):
    session_name = str(getattr(session, "name", "")).strip().lower()
    if "sprint" in session_name:
        return {"Q1": 12, "Q2": 10, "Q3": 8}
    return {"Q1": 18, "Q2": 15, "Q3": 12}


def _qualifying_phase_rows(session, phase):
    results = _qualifying_driver_rows(session)
    if results is None or results.empty:
        return []

    phase_code = str(phase or "Q1").strip().upper()
    if phase_code not in {"Q1", "Q2", "Q3"}:
        phase_code = "Q1"

    rows = []
    ordered = results.copy() if phase_code == "Q1" else results[results[phase_code].notna()].copy() if phase_code in results.columns else results.copy()
    if phase_code in ordered.columns:
        ordered = ordered.sort_values(by=phase_code, na_position="last")
    for _, row in ordered.iterrows():
        driver_number = _clean_value(row.get("DriverNumber", "")).strip()
        abbreviation = _clean_value(row.get("Abbreviation", "")).strip()
        full_name = _clean_value(row.get("FullName", "-"))
        driver_id = _clean_value(row.get("DriverId", driver_number or abbreviation)).strip() or (driver_number or abbreviation)
        driver_key = driver_number or abbreviation or _clean_value(row.get("Driver", "")).strip() or _clean_value(row.get("FullName", "")).strip() or driver_id
        team_name = _clean_value(row.get("TeamName", "-"))
        phase_time = _format_lap_time(row.get(phase_code, None))
        phase_seconds = _lap_time_seconds(row.get(phase_code, None))
        if phase_code == "Q1" and phase_seconds is None and driver_number:
            try:
                driver_runs = _qualifying_run_laps(session, driver_number)
                phase_windows = _qualifying_phase_windows(session)
                q2_start = phase_windows.get("Q2")
            except Exception:
                driver_runs = []
                q2_start = None
            q1_run_seconds = [
                run.get("flying_time_seconds")
                for run in driver_runs
                if run.get("flying_time_seconds") is not None
                and run.get("start_seconds") is not None
                and run.get("end_seconds") is not None
                and (q2_start is None or (_qualifying_run_flying_seconds(run) is not None and float(_qualifying_run_flying_seconds(run)) < float(q2_start)))
            ]
            if q1_run_seconds:
                phase_seconds = min(q1_run_seconds)
                phase_time = _format_lap_time(pd.to_timedelta(phase_seconds, unit="s"))
        if phase_seconds is None:
            phase_time = "No Time"
        headshot_url = _driver_headshot_url(driver_id, row.get("HeadshotUrl", ""))
        team_badge_text, team_badge_color = _team_badge(team_name)
        rows.append(
            {
                "value": driver_key,
                "driver_number": driver_number or driver_key,
                "driver_id": driver_id,
                "abbreviation": abbreviation,
                "full_name": full_name,
                "team_name": team_name,
                "team_badge_text": team_badge_text,
                "team_badge_color": team_badge_color,
                "headshot_url": headshot_url,
                "phase_time": phase_time,
                "phase_seconds": phase_seconds,
                "position": _clean_value(row.get("Position", "-")),
                "label": f"{abbreviation} - {full_name}",
            }
        )
    return rows


def _qualifying_phase_fastest_lap(session, phase, phase_rows=None, phase_windows=None):
    if not session:
        return None

    phase_code = str(phase or "Q1").strip().upper()
    if phase_code not in {"Q1", "Q2", "Q3"}:
        return None

    rows = phase_rows if phase_rows is not None else _qualifying_phase_rows(session, phase_code)
    if not rows:
        return None

    timed_rows = [row for row in rows if row.get("phase_seconds") is not None]
    if not timed_rows:
        return None

    fastest_row = min(
        timed_rows,
        key=lambda row: (
            float(row.get("phase_seconds")),
            int(float(row.get("position", "999999"))) if str(row.get("position", "")).strip().replace(".", "", 1).isdigit() else 999999,
        ),
    )

    fastest_driver = _normalize_driver_number(fastest_row.get("driver_number", "")) or _normalize_driver_number(fastest_row.get("DriverNumber", ""))
    if not fastest_driver:
        return None

    driver_laps = _safe_driver_laps(session, fastest_driver)
    if driver_laps is None or driver_laps.empty:
        return None

    timed_laps = driver_laps[driver_laps["LapTime"].notna()].copy() if "LapTime" in driver_laps.columns else driver_laps.copy()
    if timed_laps.empty:
        return None

    phase_start = None
    phase_end = None
    if isinstance(phase_windows, dict):
        phase_window = phase_windows.get(phase_code, {}) or {}
        try:
            phase_start = float(phase_window.get("start")) if phase_window.get("start") is not None else None
        except (TypeError, ValueError):
            phase_start = None
        try:
            phase_end = float(phase_window.get("end")) if phase_window.get("end") is not None else None
        except (TypeError, ValueError):
            phase_end = None

    if phase_start is not None and phase_end is not None:
        def lap_overlaps_phase(row):
            lap_start = _lap_time_seconds(row.get("LapStartTime", None))
            lap_end = _lap_time_seconds(row.get("Time", None))
            if lap_start is None and lap_end is None:
                return False
            if lap_start is None:
                lap_start = lap_end
            if lap_end is None:
                lap_end = lap_start
            try:
                return float(lap_start) < phase_end and float(lap_end) > phase_start
            except (TypeError, ValueError):
                return False

        filtered = timed_laps[timed_laps.apply(lap_overlaps_phase, axis=1)]
        if not filtered.empty:
            timed_laps = filtered

    phase_seconds = fastest_row.get("phase_seconds")
    if phase_seconds is not None:
        try:
            phase_seconds = float(phase_seconds)
        except (TypeError, ValueError):
            phase_seconds = None

    if phase_seconds is not None:
        def lap_delta(row):
            lap_time_seconds = _lap_time_seconds(row.get("LapTime", None))
            if lap_time_seconds is None:
                return float("inf")
            try:
                return abs(float(lap_time_seconds) - phase_seconds)
            except (TypeError, ValueError):
                return float("inf")

        timed_laps = timed_laps.copy()
        timed_laps["_phase_delta"] = timed_laps.apply(lap_delta, axis=1)
        sort_columns = ["_phase_delta"]
        if "LapNumber" in timed_laps.columns:
            sort_columns.append("LapNumber")
        if "Time" in timed_laps.columns:
            sort_columns.append("Time")
        timed_laps = timed_laps.sort_values(by=sort_columns, na_position="last")
    else:
        sort_columns = [column for column in ["LapNumber", "Time"] if column in timed_laps.columns]
        if sort_columns:
            timed_laps = timed_laps.sort_values(by=sort_columns, na_position="last")

    if timed_laps.empty:
        return None

    best_row = timed_laps.iloc[0]
    lap_number = best_row.get("LapNumber")
    if pd.isna(lap_number):
        return None

    try:
        return _resolve_lap(session, fastest_driver, f"{fastest_driver}:{int(float(lap_number))}")
    except Exception:
        try:
            return timed_laps.iloc[0]
        except Exception:
            return None


def _lap_options(session, driver_number):
    if not driver_number:
        return []

    driver_laps = _safe_driver_laps(session, driver_number)
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

    driver_laps = _safe_driver_laps(session, driver_number)
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

    driver_laps = _safe_driver_laps(session, driver_number)
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

    driver_laps = _safe_driver_laps(session, driver_number)
    if driver_laps is None or driver_laps.empty:
        return []

    valid = driver_laps.copy().sort_values(by=["LapNumber", "Time"])
    runs = []
    current_run = None
    run_number = 0

    def finish_run(run):
        if not run or not run["laps"]:
            return None
        _qualifying_finalize_run_laps(run["laps"])
        start_seconds = next(
            (
                lap.get("pit_out_seconds")
                for lap in run["laps"]
                if lap.get("lap_type") in {"Out Lap", "Out / In Lap"} and lap.get("pit_out_seconds") is not None
            ),
            next((lap.get("lap_start_seconds") for lap in run["laps"] if lap.get("lap_start_seconds") is not None), None),
        )
        end_seconds = next((lap.get("lap_end_seconds") for lap in reversed(run["laps"]) if lap.get("lap_end_seconds") is not None), None)
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
            "representative_start_seconds": next(
                (
                    lap.get("lap_start_seconds")
                    for lap in run["laps"]
                    if lap.get("lap_type") in {"Flying Lap", "Cool Lap"} and lap.get("lap_start_seconds") is not None
                ),
                next((lap.get("lap_start_seconds") for lap in run["laps"] if lap.get("lap_start_seconds") is not None), start_seconds),
            ),
            "representative": next(
                (lap["value"] for lap in run["laps"] if lap["lap_type"] == "Flying Lap" and lap["lap_time_seconds"] is not None),
                next((lap["value"] for lap in run["laps"] if lap["lap_time_seconds"] is not None), run["laps"][0]["value"]),
            ),
            "start_seconds": start_seconds,
            "end_seconds": end_seconds,
            "duration_seconds": max((end_seconds or 0) - (start_seconds or 0), 1.0) if start_seconds is not None and end_seconds is not None else None,
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
                "lap_start_seconds": _lap_time_seconds(row.get("LapStartTime", None)),
                "lap_end_seconds": _lap_time_seconds(row.get("Time", None)),
                "pit_out_seconds": _lap_time_seconds(row.get("PitOutTime", None)),
                "pit_in_seconds": _lap_time_seconds(row.get("PitInTime", None)),
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


def _qualifying_run_flying_seconds(run):
    if not run:
        return None

    start_seconds = next(
        (
            lap.get("lap_start_seconds")
            for lap in run.get("laps", [])
            if lap.get("lap_type") in {"Flying Lap", "Cool Lap"} and lap.get("lap_start_seconds") is not None
        ),
        None,
    )
    try:
        return float(start_seconds) if start_seconds is not None else None
    except (TypeError, ValueError):
        return None


def _qualifying_timeline_graph(session, phase=None, split_sections=True):
    event = getattr(session, "event", None)
    timing_session = session

    driver_rows_source = _qualifying_driver_rows(timing_session)
    if driver_rows_source is None or driver_rows_source.empty:
        driver_rows_source = _qualifying_driver_rows(session)
    if driver_rows_source is None or driver_rows_source.empty:
        return None

    requested_phase = str(phase or "Q1").strip().upper()
    if requested_phase not in {"Q1", "Q2", "Q3"}:
        requested_phase = "Q1"
    use_phase_sections = split_sections
    phase_durations = _qualifying_phase_durations(timing_session)

    def run_outlap_seconds(run):
        start_seconds = run.get("start_seconds")
        if start_seconds is None:
            start_seconds = next(
                (
                    lap.get("pit_out_seconds")
                    for lap in run.get("laps", [])
                    if lap.get("lap_type") in {"Out Lap", "Out / In Lap"} and lap.get("pit_out_seconds") is not None
                ),
                None,
            )
        if start_seconds is None:
            start_seconds = next(
                (
                    lap.get("lap_start_seconds")
                    for lap in run.get("laps", [])
                    if lap.get("lap_type") in {"Out Lap", "Out / In Lap"} and lap.get("lap_start_seconds") is not None
                ),
                None,
            )
        if start_seconds is None:
            start_seconds = next(
                (
                    lap.get("lap_time_seconds")
                    for lap in run.get("laps", [])
                    if lap.get("lap_type") == "Flying Lap" and lap.get("lap_time_seconds") is not None
                ),
                None,
            )
        try:
            return float(start_seconds) if start_seconds is not None else None
        except (TypeError, ValueError):
            return None

    def infer_qualifying_phase_windows_from_runs():
        run_records = []
        for _, row in driver_rows_source.iterrows():
            driver_number = _clean_value(row.get("DriverNumber", "")).strip()
            if not driver_number:
                driver_number = _clean_value(row.get("driver_number", "")).strip()
            if not driver_number:
                continue
            for run in _qualifying_run_laps(timing_session, driver_number):
                outlap_start = run_outlap_seconds(run)
                flying_start = _qualifying_run_flying_seconds(run)
                inlap_start = next(
                    (
                        lap.get("lap_start_seconds")
                        for lap in run.get("laps", [])
                        if lap.get("lap_type") == "In Lap" and lap.get("lap_start_seconds") is not None
                    ),
                    None,
                )
                if outlap_start is None or flying_start is None:
                    continue
                try:
                    outlap_start = float(outlap_start)
                    flying_start = float(flying_start)
                    inlap_start = float(inlap_start) if inlap_start is not None else None
                except (TypeError, ValueError):
                    continue
                run_records.append(
                    {
                        "driver_number": driver_number,
                        "run_ref": run.get("representative", f"{driver_number}:{run.get('run_number', '')}"),
                        "outlap_start": outlap_start,
                        "flying_start": flying_start,
                        "inlap_start": inlap_start,
                        "end_seconds": float(run.get("end_seconds")) if run.get("end_seconds") is not None else None,
                    }
                )
        if not run_records:
            return {}

        def run_sort_key(record):
            outlap_start = record.get("outlap_start")
            flying_start = record.get("flying_start")
            return (
                float(outlap_start) if outlap_start is not None else float("inf"),
                float(flying_start) if flying_start is not None else float("inf"),
            )

        def phase_marker(record):
            marker = record.get("flying_start")
            if marker is None:
                marker = record.get("inlap_start")
            return marker

        def select_phase(runs, duration_minutes):
            if not runs:
                return None, [], [], None, None

            ordered_runs = sorted(runs, key=run_sort_key)
            anchor = ordered_runs[0]
            phase_start = anchor["outlap_start"]
            phase_end = phase_start + (duration_minutes * 60.0)
            selected = [anchor]
            remaining = []

            for record in ordered_runs[1:]:
                marker = phase_marker(record)
                if marker is None:
                    remaining.append(record)
                    continue

                marker_value = float(marker)
                if float(phase_start) <= marker_value < float(phase_end):
                    selected.append(record)
                else:
                    remaining.append(record)

            return anchor, selected, remaining, phase_end, phase_start

        def run_ref_set(records):
            return {record.get("run_ref") for record in records if record.get("run_ref")}

        run_records.sort(key=run_sort_key)
        q1_duration = phase_durations.get("Q1", 18)
        q2_duration = phase_durations.get("Q2", 15)
        q3_duration = phase_durations.get("Q3", 12)

        q1_anchor, q1_runs, remaining_after_q1, q1_end_marker, q1_start_phase = select_phase(run_records, q1_duration)
        q1_start = q1_start_phase if q1_start_phase is not None else (q1_anchor["outlap_start"] if q1_anchor is not None else None)
        q1_end = q1_start + (q1_duration * 60.0) if q1_start is not None else None

        q2_window_anchor, q2_window_runs, remaining_after_q2, q2_end_marker, q2_start_phase = select_phase(remaining_after_q1, q2_duration)
        q2_start = q2_start_phase if q2_start_phase is not None else (q2_window_anchor["outlap_start"] if q2_window_anchor is not None else None)
        q2_end = q2_start + (q2_duration * 60.0) if q2_start is not None else None

        q3_anchor, q3_runs, _, _, q3_start_phase = select_phase(remaining_after_q2, q3_duration)
        q3_start = q3_start_phase if q3_start_phase is not None else (q3_anchor["outlap_start"] if q3_anchor is not None else q2_end)
        q3_end = q3_start + (q3_duration * 60.0) if q3_start is not None else None
        return {
            "Q1": {"start": q1_start, "end": q1_end, "anchor": q1_anchor, "runs": list(run_ref_set(q1_runs))},
            "Q2": {"start": q2_start, "end": q2_end, "anchor": q2_window_anchor, "runs": list(run_ref_set(q2_window_runs))},
            "Q3": {"start": q3_start, "end": q3_end, "anchor": q3_anchor, "runs": list(run_ref_set(q3_runs))},
        }

    phase_windows = infer_qualifying_phase_windows_from_runs()
    phase_window = phase_windows.get(requested_phase, {}) if isinstance(phase_windows, dict) else {}
    phase_start = phase_window.get("start")
    phase_end = phase_window.get("end")
    phase_run_refs = set(phase_window.get("runs") or [])
    def run_phase_code(run, driver_number=None):
        run_ref = run.get("representative", "")
        if run_ref and run_ref in phase_run_refs:
            return requested_phase
        return None

    def assign_run_phase(run, driver_number):
        return {**run, "phase_code": run_phase_code(run, driver_number)}

    def segment_from_lap(lap):
        start_seconds = lap.get("lap_start_seconds")
        end_seconds = lap.get("lap_end_seconds")
        pit_out_seconds = lap.get("pit_out_seconds")
        pit_in_seconds = lap.get("pit_in_seconds")
        lap_type = str(lap.get("lap_type", "Flying Lap"))
        if pit_out_seconds is not None and lap_type in {"Out Lap", "Out / In Lap"}:
            start_seconds = pit_out_seconds
        if pit_in_seconds is not None and lap_type in {"In Lap", "Out / In Lap"}:
            end_seconds = pit_in_seconds
        if start_seconds is None or end_seconds is None:
            return None
        start_seconds = max(0.0, float(start_seconds))
        end_seconds = max(start_seconds + 0.5, float(end_seconds))
        return {
            "start_seconds": start_seconds,
            "end_seconds": end_seconds,
            "duration_seconds": max(end_seconds - start_seconds, 0.5),
            "lap_type": lap_type,
            "lap_number": lap.get("lap_number", "1"),
        }

    def build_driver_row(row, phase_code, runs):
        driver_number = _clean_value(row.get("driver_number", "")).strip()
        if not driver_number:
            driver_number = _clean_value(row.get("DriverNumber", "")).strip()
        if not driver_number:
            return None

        rendered_runs = []
        for index, run in enumerate(runs, start=1):
            laps = run.get("laps", [])
            if not laps:
                continue

            segments = [segment_from_lap(lap) for lap in laps]
            segments = [segment for segment in segments if segment is not None]
            run_start = run.get("start_seconds")
            run_end = run.get("end_seconds")
            if run_start is None and segments:
                run_start = min(segment["start_seconds"] for segment in segments)
            if run_end is None and segments:
                run_end = max(segment["end_seconds"] for segment in segments)
            if run_start is None or run_end is None:
                continue

            run_start = max(0.0, float(run_start))
            run_end = max(run_start + 0.5, float(run_end))
            if not segments:
                segments = [
                    {
                        "start_seconds": run_start,
                        "end_seconds": run_end,
                        "duration_seconds": max(run_end - run_start, 0.5),
                        "lap_type": "Flying Lap",
                        "lap_number": "1",
                    }
                ]

            rendered_runs.append(
                {
                    "run_number": index,
                    "phase_code": phase_code,
                    "lap_count": len(laps),
                    "start_seconds": run_start,
                    "end_seconds": run_end,
                    "duration_seconds": max(run_end - run_start, 0.5),
            "representative": run.get("representative", f"{driver_number}:{index}"),
            "representative_lap_time": run.get("flying_time", "-"),
            "representative_lap_time_seconds": run.get("flying_time_seconds"),
            "representative_lap_number": str(run.get("representative", f"{driver_number}:{index}")).split(":", 1)[-1],
            "compound": next((lap.get("compound") for lap in laps if lap.get("compound")), "-"),
            "segments": segments,
            "representative_start_seconds": run.get("representative_start_seconds"),
        }
            )

        time_label = _clean_value(row.get("time_label", row.get("result_time", "-"))).strip() or "-"
        if phase_code != "ALL":
            phase_time = _clean_value(row.get("phase_time", "-")).strip() or "-"
            phase_seconds = row.get("phase_seconds")
            position = str(row.get("position", "")).strip()
            if position == "1":
                time_label = phase_time
            elif phase_seconds is not None and phase_seconds != "-":
                leader_seconds = row.get("_leader_seconds")
                if leader_seconds is not None:
                    gap = max(float(phase_seconds) - float(leader_seconds), 0.0)
                    time_label = f"+{_format_lap_time(pd.to_timedelta(gap, unit='s'))}"
                else:
                    time_label = phase_time
            else:
                time_label = phase_time

        return {
            "value": row.get("value", row.get("DriverNumber", driver_number)),
            "driver_number": driver_number,
            "driver": row.get("full_name", row.get("driver", row.get("FullName", ""))),
            "abbreviation": row.get("abbreviation", row.get("Abbreviation", driver_number)),
            "team": row.get("team_name", row.get("TeamName", "-")),
            "color": _team_color(row.get("team_name", row.get("TeamName", "-"))),
            "time_label": time_label,
            "runs": rendered_runs,
        }

    sections = []
    run_ends = []
    run_starts = []
    all_run_ends = []
    all_run_starts = []
    if use_phase_sections:
        phase_code = requested_phase
        rows_source = _qualifying_phase_rows(timing_session, phase_code)
        if rows_source:
            leader_seconds = next((row.get("phase_seconds") for row in rows_source if str(row.get("position", "")).strip() == "1" and row.get("phase_seconds") is not None), None)
            phase_drivers = []
            for row in rows_source:
                row = dict(row)
                row["_leader_seconds"] = leader_seconds
                driver_number = _clean_value(row.get("driver_number", "")).strip()
                if not driver_number:
                    driver_number = _clean_value(row.get("DriverNumber", "")).strip()
                if not driver_number:
                    continue
                runs = _qualifying_run_laps(timing_session, driver_number)
                phase_runs = []
                for run in runs:
                    if run.get("start_seconds") is not None:
                        all_run_starts.append(run["start_seconds"])
                    if run.get("end_seconds") is not None:
                        all_run_ends.append(run["end_seconds"])
                    run_start = run.get("start_seconds")
                    run_end = run.get("end_seconds")
                    run_ref = run.get("representative", f"{driver_number}:{run.get('run_number', '')}")
                    if run_ref in phase_run_refs:
                        phase_run = assign_run_phase(run, driver_number)
                        phase_runs.append(phase_run)
                if not phase_runs and row.get("phase_seconds") is None:
                    continue
                driver_row = build_driver_row(row, phase_code, phase_runs)
                if driver_row is not None:
                    phase_drivers.append(driver_row)
                    run_starts.extend(run["start_seconds"] for run in phase_runs if run.get("start_seconds") is not None)
                    run_ends.extend(run["end_seconds"] for run in phase_runs if run.get("end_seconds") is not None)

            sections.append(
                {
                    "phase_code": phase_code,
                    "title": phase_code,
                    "participant_count": len(phase_drivers),
                    "eliminated_count": 0,
                    "max_time": 0.0,
                    "drivers": phase_drivers,
                }
            )
    else:
        combined_drivers = []
        for _, row in driver_rows_source.iterrows():
            row_data = row.to_dict()
            row_data["_leader_seconds"] = None
            driver_number = _clean_value(row_data.get("driver_number", "")).strip()
            if not driver_number:
                driver_number = _clean_value(row_data.get("DriverNumber", "")).strip()
            if not driver_number:
                continue
            runs = _qualifying_run_laps(timing_session, driver_number)
            for run in runs:
                if run.get("start_seconds") is not None:
                    all_run_starts.append(run["start_seconds"])
                if run.get("end_seconds") is not None:
                    all_run_ends.append(run["end_seconds"])
            phase_runs = [assign_run_phase(run, driver_number) for run in runs]
            driver_row = build_driver_row(row_data, "ALL", phase_runs)
            if driver_row is not None:
                combined_drivers.append(driver_row)
                run_starts.extend(run["start_seconds"] for run in phase_runs if run.get("start_seconds") is not None)
                run_ends.extend(run["end_seconds"] for run in phase_runs if run.get("end_seconds") is not None)
        sections.append(
            {
                "phase_code": "ALL",
                "title": "Qualifying",
                "participant_count": len(combined_drivers),
                "eliminated_count": 0,
                "max_time": 0.0,
                "drivers": combined_drivers,
            }
        )

    drivers = [driver for section in sections for driver in (section.get("drivers") or [])]
    if not drivers:
        return None

    fastest_run = None
    for section in sections:
        for driver in section.get("drivers") or []:
            for run in driver.get("runs") or []:
                time_seconds = run.get("representative_lap_time_seconds")
                start_seconds = run.get("representative_start_seconds")
                if time_seconds is None or start_seconds is None:
                    continue
                candidate = {
                    "driver": driver.get("abbreviation") or driver.get("driver") or driver.get("value") or "",
                    "driver_label": driver.get("driver") or driver.get("abbreviation") or driver.get("value") or "",
                    "time_seconds": float(time_seconds),
                    "start_seconds": float(start_seconds),
                    "display_time": run.get("representative_lap_time") or "-",
                }
                if fastest_run is None or candidate["time_seconds"] < fastest_run["time_seconds"]:
                    fastest_run = candidate

    timeline_start = min(all_run_starts) if all_run_starts else (min(run_starts) if run_starts else 0.0)
    session_end = max(all_run_ends) if all_run_ends else (max(run_ends) if run_ends else timeline_start + 1.0)
    if use_phase_sections:
        phase_start_value = phase_start if phase_start is not None else timeline_start
        phase_end_value = phase_end if phase_end is not None else session_end
        visible_end = max(run_ends) if run_ends else session_end
        if phase_end_value > phase_start_value:
            timeline_start = float(phase_start_value)
            session_end = float(max(phase_end_value, visible_end))
    section_max_time = session_end - timeline_start
    section_max_time = max(float(section_max_time), 1.0)
    for section in sections:
        section["max_time"] = section_max_time
    graph = {
        "title": "Qualifying run timeline",
        "phase_code": requested_phase if use_phase_sections else "ALL",
        "max_time": section_max_time,
        "time_offset": timeline_start,
        "fastest_run": fastest_run,
        "phase_windows": phase_windows,
        "sections": sections,
        "drivers": drivers,
    }

    return graph


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


def _normalize_driver_number(driver_number):
    driver_number = _clean_value(driver_number).strip()
    if not driver_number or driver_number == "-":
        return ""
    try:
        return str(int(float(driver_number)))
    except (TypeError, ValueError):
        return driver_number


def _wait_for_session_laps(session, attempts=20, delay=0.5):
    if not session:
        return None

    laps = None
    for attempt in range(attempts):
        try:
            laps = session.laps
            if laps is not None:
                return laps
        except Exception:
            laps = None

        if attempt == 0:
            if not _load_session_with_timeout(session):
                break

        if attempt < attempts - 1:
            time.sleep(delay)

    return laps


def _load_session_with_timeout(
    session,
    timeout_seconds=_SESSION_LOAD_TIMEOUT_SECONDS,
    load_laps=True,
    load_telemetry=False,
):
    if not session:
        return False

    finished = Event()
    load_error = {}

    def worker():
        try:
            session.load(
                laps=load_laps,
                telemetry=load_telemetry,
                weather=False,
                messages=False,
            )
        except Exception as exc:
            load_error["exc"] = exc
        finally:
            finished.set()

    Thread(target=worker, daemon=True).start()
    finished.wait(timeout_seconds)
    if not finished.is_set():
        return False
    if load_error.get("exc") is not None:
        raise load_error["exc"]
    return True


def _cached_session_laps(session):
    if not session:
        return None

    cached = getattr(session, "_codex_cached_laps", None)
    if cached is not None:
        return cached

    try:
        existing_laps = session.laps
    except Exception:
        existing_laps = None
    if existing_laps is not None and not getattr(existing_laps, "empty", True):
        try:
            setattr(session, "_codex_cached_laps", existing_laps)
        except Exception:
            pass
        return existing_laps

    if not _load_session_with_timeout(session):
        return None

    laps = _wait_for_session_laps(session, attempts=10, delay=0.5)

    try:
        setattr(session, "_codex_cached_laps", laps)
    except Exception:
        pass
    return laps


def _safe_driver_laps(session, driver_number):
    if not session or not driver_number:
        return None

    normalized_driver_number = _normalize_driver_number(driver_number)
    if not normalized_driver_number:
        return None

    try:
        driver_laps = _cached_session_laps(session)
    except Exception:
        return None

    if driver_laps is None or driver_laps.empty:
        return None

    if "DriverNumber" in driver_laps.columns:
        normalized_numbers = driver_laps["DriverNumber"].map(_normalize_driver_number)
        filtered = driver_laps[normalized_numbers == normalized_driver_number]
        if not filtered.empty:
            return filtered

    if "Driver" in driver_laps.columns:
        driver_code = normalized_driver_number.upper()
        filtered = driver_laps[driver_laps["Driver"].astype(str).str.strip().str.upper() == driver_code]
        if not filtered.empty:
            return filtered

    try:
        driver_laps = session.laps.pick_drivers(normalized_driver_number)
        if driver_laps is not None and not driver_laps.empty:
            return driver_laps
    except Exception:
        pass

    return None


def _safe_session_laps(session):
    if not session:
        return None

    try:
        laps = _cached_session_laps(session)
        if laps is not None:
            return laps
    except Exception:
        pass
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

    driver_laps = _safe_driver_laps(session, driver_number)
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


def _default_stint_lap_value(stint_data):
    laps = (stint_data or {}).get("laps") or []
    if not laps:
        return ""

    timed_laps = [lap for lap in laps if lap.get("lap_time_seconds") is not None and lap.get("value")]
    candidate_laps = timed_laps or [lap for lap in laps if lap.get("value")]
    if not candidate_laps:
        return ""

    chosen_lap = min(
        candidate_laps,
        key=lambda lap: (
            float(lap.get("lap_time_seconds") or float("inf")),
            float(lap.get("lap_number") or float("inf")),
        ),
    )
    return _clean_value(chosen_lap.get("value", ""))


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
    deleted_reason = _clean_value(lap.get("DeletedReason", "-")).strip()
    deleted_reason = "" if deleted_reason in {"", "-"} else deleted_reason
    is_deleted = _is_truthy_value(lap.get("Deleted", False))
    summary = {
        "driver": _clean_value(lap.get("Driver", "-")),
        "team": _clean_value(lap.get("Team", "-")),
        "lap_number": _clean_value(lap.get("LapNumber", "-")),
        "lap_time": _format_lap_time(lap.get("LapTime", "-")),
        "compound": _clean_value(lap.get("Compound", "-")),
        "stint": _clean_value(lap.get("Stint", "-")),
        "tyre_life": _clean_value(lap.get("TyreLife", "-")),
        "samples": len(telemetry),
        "is_deleted": is_deleted,
        "deleted_reason": deleted_reason,
        "deleted_label": f"Deleted ({deleted_reason})" if is_deleted and deleted_reason else ("Deleted" if is_deleted else ""),
    }
    if "Speed" in telemetry.columns and not telemetry.empty:
        summary["max_speed"] = _clean_value(telemetry["Speed"].max())
    else:
        summary["max_speed"] = "-"
    return summary


def _lap_record(lap, telemetry):
    gap_ahead = "-"
    if "DistanceToDriverAhead" in telemetry.columns and "Speed" in telemetry.columns:
        gap_points = []
        time_series = telemetry["Time"].dt.total_seconds() if "Time" in telemetry.columns else None
        for time_value, distance_value, speed_value in zip(time_series if time_series is not None else [], telemetry["DistanceToDriverAhead"], telemetry["Speed"]):
            gap_seconds = _gap_ahead_seconds(distance_value, speed_value)
            if gap_seconds is None or pd.isna(time_value):
                continue
            gap_points.append([float(time_value), float(gap_seconds)])
        if gap_points:
            gap_ahead = _format_gap_seconds(gap_points[-1][1])

    fields = [
        ("tyre_life", "Tyre Age", _clean_value(lap.get("TyreLife", "-"))),
        ("track_status", "Track Status", _format_track_status(lap.get("TrackStatus", "-"))),
        ("position", "Position", _clean_value(lap.get("Position", "-"))),
    ]

    return fields


def _race_stints(session, driver_number, session_code):
    if not session or not driver_number or not _supports_pit_strategy_graph(session_code):
        return None

    driver_laps = _safe_driver_laps(session, driver_number)
    driver_name = _clean_value(driver_number)
    stints = []

    if driver_laps is not None and not driver_laps.empty:
        if "Driver" in driver_laps.columns:
            driver_name = _clean_value(driver_laps.iloc[0].get("Driver", driver_name))

        ordered = driver_laps[driver_laps["LapNumber"].notna()].copy() if "LapNumber" in driver_laps.columns else pd.DataFrame()
        if not ordered.empty and "Stint" in ordered.columns:
            ordered = ordered.sort_values(by=["LapNumber", "Time"])
            lap_position_lookup = {}
            if _supports_race_position_markers(session_code) and "Position" in ordered.columns:
                previous_position = None
                for _, lap_row in ordered.iterrows():
                    lap_number = lap_row.get("LapNumber")
                    if pd.isna(lap_number):
                        continue

                    position_value = lap_row.get("Position")
                    current_position = None
                    if pd.notna(position_value):
                        try:
                            current_position = int(float(position_value))
                        except (TypeError, ValueError):
                            current_position = None

                    position_change = None
                    position_before = None
                    position_after = None
                    if current_position is not None and previous_position is not None and current_position != previous_position:
                        position_change = previous_position - current_position
                        position_before = previous_position
                        position_after = current_position

                    if current_position is not None:
                        previous_position = current_position

                    lap_position_lookup[int(float(lap_number))] = {
                        "position": current_position,
                        "position_change": position_change,
                        "position_before": position_before,
                        "position_after": position_after,
                    }

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
                    pit_out = pd.notna(lap_row.get("PitOutTime", None))
                    pit_in = pd.notna(lap_row.get("PitInTime", None))
                    if pit_out and pit_in:
                        lap_role = "Out / In Lap"
                    elif pit_out:
                        lap_role = "Out Lap"
                    elif pit_in:
                        lap_role = "In Lap"
                    else:
                        lap_role = "Flying Lap"
                    lap_position = lap_position_lookup.get(lap_number_int, {})
                    position_change = lap_position.get("position_change")
                    position_change_text = None
                    position_change_direction = None
                    if position_change is not None and position_change != 0:
                        direction = "gain" if position_change > 0 else "loss"
                        position_change_direction = direction
                        position_change_text = f"{int(position_change):+d}"
                    laps.append(
                        {
                            "lap_number": lap_number_int,
                            "lap_time": _clean_value(lap_time),
                            "lap_time_seconds": lap_time_seconds,
                            "lap_start_seconds": _lap_time_seconds(lap_row.get("LapStartTime", None)),
                            "lap_end_seconds": _lap_time_seconds(lap_row.get("Time", None)),
                            "value": f"{_clean_value(driver_number)}:{lap_number_int}",
                            "compound": _clean_value(lap_row.get("Compound", "-")),
                            "fresh_tyre": bool(lap_row.get("FreshTyre", False)) if pd.notna(lap_row.get("FreshTyre", False)) else False,
                            "is_personal_best": bool(lap_row.get("IsPersonalBest", False)) if pd.notna(lap_row.get("IsPersonalBest", False)) else False,
                            "lap_type": lap_role,
                            "pit_out_seconds": _lap_time_seconds(lap_row.get("PitOutTime", None)),
                            "pit_in_seconds": _lap_time_seconds(lap_row.get("PitInTime", None)),
                            "position": lap_position.get("position"),
                            "position_change": position_change,
                            "position_change_text": position_change_text,
                            "position_change_direction": position_change_direction,
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

        pit_stop_max_seconds = None

        def _pit_stop_seconds(previous_stint, current_stint):
            previous_laps = previous_stint.get("laps") or []
            current_laps = current_stint.get("laps") or []
            if not previous_laps or not current_laps:
                return None

            previous_lap = previous_laps[-1]
            current_lap = current_laps[0]

            candidates = []
            previous_pit_in = previous_lap.get("pit_in_seconds")
            current_pit_out = current_lap.get("pit_out_seconds")
            if previous_pit_in is not None and current_pit_out is not None:
                try:
                    candidates.append(max(0.0, float(current_pit_out) - float(previous_pit_in)))
                except (TypeError, ValueError):
                    pass

            previous_lap_end = previous_lap.get("lap_end_seconds")
            current_lap_start = current_lap.get("lap_start_seconds")
            if previous_lap_end is not None and current_lap_start is not None:
                try:
                    candidates.append(max(0.0, float(current_lap_start) - float(previous_lap_end)))
                except (TypeError, ValueError):
                    pass

            for candidate in candidates:
                if candidate is not None:
                    return candidate
            return None

        for index, stint in enumerate(stints):
            pit_stop_seconds = _pit_stop_seconds(stints[index - 1], stint) if index > 0 else None
            stint["pit_stop_seconds"] = pit_stop_seconds
            stint["pit_stop_time"] = _format_minutes_seconds(pit_stop_seconds) if pit_stop_seconds is not None else "-"
            if pit_stop_seconds is not None:
                pit_stop_max_seconds = max(pit_stop_max_seconds or 0.0, float(pit_stop_seconds))

        for stint in stints:
            pit_stop_seconds = stint.get("pit_stop_seconds")
            if pit_stop_seconds is None or not pit_stop_max_seconds:
                stint["pit_stop_fill_percent"] = None if pit_stop_seconds is None else 100.0
                continue
            stint["pit_stop_fill_percent"] = max(28.0, min(100.0, (float(pit_stop_seconds) / pit_stop_max_seconds) * 100.0))

    if not stints:
        results = getattr(session, "results", None)
        if results is None or results.empty:
            return None

        result_row = results[results["DriverNumber"].astype(str).str.strip() == str(driver_number).strip()]
        if result_row.empty:
            return None

        row = result_row.iloc[0]
        lap_total = int(float(row.get("Laps", 0) or 0))
        if lap_total <= 0:
            return None

        driver_name = _clean_value(row.get("FullName", driver_name))
        inferred_compound = "-"
        if driver_laps is not None and not driver_laps.empty and "Compound" in driver_laps.columns:
            compound_series = driver_laps["Compound"].dropna().astype(str)
            compound_series = compound_series[compound_series.str.strip() != ""]
            if not compound_series.empty:
                inferred_compound = compound_series.iloc[0]
        placeholder_laps = [
            {
                "lap_number": lap_number,
                "lap_time": "-",
                "lap_time_seconds": None,
                "value": f"{_clean_value(driver_number)}:{lap_number}",
                "compound": _clean_value(inferred_compound),
                "fresh_tyre": False,
                "is_personal_best": False,
                "lap_type": "Flying Lap",
            }
            for lap_number in range(1, lap_total + 1)
        ]
        stints = [
            {
                "stint": 1,
                "start_lap": 1,
                "end_lap": lap_total,
                "lap_count": lap_total,
                "compound": _clean_value(inferred_compound),
                "tyre_color": _tyre_color(inferred_compound),
                "fresh_tyre": False,
                "is_personal_best": False,
                "laps": placeholder_laps,
            }
        ]

    return {
        "driver": driver_name,
        "stints": stints,
        "lap_total": int(sum(item["lap_count"] for item in stints)),
    }


def _race_pit_stop_average_seconds(stint_cache, exclude_driver_number=None):
    if not stint_cache:
        return None

    excluded_driver = _normalize_driver_number(exclude_driver_number) if exclude_driver_number else ""
    driver_averages = []
    pit_stop_samples = []

    for driver_number, driver_data in stint_cache.items():
        if excluded_driver and _normalize_driver_number(driver_number) == excluded_driver:
            continue
        if not driver_data:
            continue

        driver_samples = []
        for stint in driver_data.get("stints", []):
            pit_stop_seconds = stint.get("pit_stop_seconds")
            if pit_stop_seconds is None:
                continue
            try:
                numeric = float(pit_stop_seconds)
            except (TypeError, ValueError):
                continue
            if numeric < 0:
                continue
            driver_samples.append(numeric)

        if driver_samples:
            driver_averages.append(sum(driver_samples) / len(driver_samples))
            pit_stop_samples.extend(driver_samples)

    if driver_averages:
        return sum(driver_averages) / len(driver_averages)
    if pit_stop_samples:
        return sum(pit_stop_samples) / len(pit_stop_samples)
    return None


def _pit_strategy_graph(session):
    if not session:
        return None

    results = getattr(session, "results", None)
    driver_meta = _driver_options(session, results=results)
    if not driver_meta:
        return None
    practice_mode = results is None or results.empty

    rows = []
    max_lap = 0

    def format_session_time(value, prefix_plus=False):
        text = _clean_value(value).strip()
        if not text or text == "-":
            return "-"
        text = text.replace("0 days ", "")
        text = text.replace("0:0", "")
        text = text.replace("0:", "")
        text = text.replace("days ", "")
        text = text.strip()
        if text.startswith("0:"):
            text = text[2:]
        if prefix_plus and not text.startswith("+"):
            text = f"+{text}"
        return text

    def classify_result_label(result_row, leader_laps):
        status_text = _clean_value(result_row.get("Status", "")).strip().lower()
        driver_laps = int(float(result_row.get("Laps", 0) or 0))
        if "dns" in status_text or driver_laps <= 0:
            return "DNS"
        if any(token in status_text for token in ("retired", "dnf", "disqualified", "withdrawn", "accident", "damage", "engine", "mechanical", "suspension", "gearbox", "spin", "collision", "stopped")):
            return "DNF"
        if "lapped" in status_text or driver_laps < leader_laps:
            laps_down = max(leader_laps - driver_laps, 1)
            return f"+{laps_down} Lap" if laps_down == 1 else f"+{laps_down} Laps"
        return format_session_time(result_row.get("Time", ""), prefix_plus=True)

    for driver in driver_meta:
        driver_number = driver["value"]
        laps = _safe_driver_laps(session, driver_number)
        if laps is None or laps.empty or "Stint" not in laps.columns:
            continue

        ordered = laps[laps["LapNumber"].notna()].copy()
        if ordered.empty:
            continue

        ordered = ordered.sort_values(by=["LapNumber", "Time"])
        stints = []
        for stint_number, stint_laps in ordered.groupby("Stint", dropna=True):
            stint_laps = stint_laps.sort_values(by=["LapNumber", "Time"])
            if stint_laps.empty:
                continue

            lap_numbers = stint_laps["LapNumber"].dropna().astype(int).tolist()
            if not lap_numbers:
                continue

            compound_series = stint_laps["Compound"].dropna().astype(str) if "Compound" in stint_laps.columns else pd.Series([], dtype=str)
            compound = compound_series[compound_series.str.strip() != ""].iloc[0] if not compound_series.empty else "-"

            stints.append(
                {
                    "stint": int(stint_number) if pd.notna(stint_number) else 0,
                    "start_lap": min(lap_numbers),
                    "end_lap": max(lap_numbers),
                    "lap_count": len(stint_laps),
                    "compound": _clean_value(compound),
                    "tyre_color": _tyre_color(compound),
                }
            )

        if not stints:
            continue

        pit_laps = []
        for stint in stints[1:]:
            pit_laps.append(stint["start_lap"])
        pit_laps = sorted(set(pit_laps))

        max_lap = max(max_lap, max(stint["end_lap"] for stint in stints))
        rows.append(
            {
                "driver_number": driver_number,
                "driver": driver["full_name"],
                "abbreviation": driver["abbreviation"],
                "team": driver["team_name"],
                "team_color": driver["team_badge_color"],
                "current_position": int(driver.get("position") or len(rows) + 1),
                "time_label": driver.get("result_time", "-") if practice_mode else "-",
                "stints": stints,
                "pit_laps": pit_laps,
                "driver_number": driver_number,
            }
        )

    if not rows or max_lap <= 0:
        return None

    def classify_track_status(raw_status):
        status = _clean_value(raw_status).strip()
        if not status:
            return None
        if "5" in status:
            return {"label": "Red Flag", "short": "RF", "color": "#ef4444"}
        if "4" in status:
            return {"label": "Safety Car", "short": "SC", "color": "#facc15"}
        if "6" in status or "7" in status:
            return {"label": "Virtual Safety Car", "short": "VSC", "color": "#facc15"}
        return None

    status_segments = []
    track_status = getattr(session, "track_status", None)
    laps_for_status = None
    session_laps = _safe_session_laps(session)
    if session_laps is not None and "LapNumber" in session_laps.columns and "LapStartTime" in session_laps.columns and "Time" in session_laps.columns:
        laps_for_status = session_laps[
            session_laps["LapNumber"].notna() & session_laps["LapStartTime"].notna() & session_laps["Time"].notna()
        ].copy()
        if not laps_for_status.empty:
            laps_for_status = laps_for_status.sort_values(by=["LapNumber", "Time"])

    if track_status is not None and not track_status.empty and laps_for_status is not None and not laps_for_status.empty:
        status_events = track_status.sort_values(by="Time").copy()
        status_events["end_time"] = status_events["Time"].shift(-1)
        final_lap_time = laps_for_status["Time"].max()

        current = None
        for _, row in status_events.iterrows():
            marker = classify_track_status(row.get("Status"))
            if marker is None or marker["label"] not in {"Safety Car", "Virtual Safety Car", "Red Flag"}:
                continue

            start_time = row.get("Time")
            end_time = row.get("end_time")
            if pd.isna(start_time):
                continue
            if pd.isna(end_time):
                end_time = final_lap_time
            if pd.isna(end_time) or end_time <= start_time:
                continue

            overlapping_laps = laps_for_status[
                (laps_for_status["LapStartTime"] < end_time) & (laps_for_status["Time"] > start_time)
            ]["LapNumber"].dropna()
            if overlapping_laps.empty:
                continue

            segment = {
                "start_lap": int(float(overlapping_laps.min())),
                "end_lap": int(float(overlapping_laps.max())),
                **marker,
            }

            if current is not None and current["label"] == segment["label"] and segment["start_lap"] <= current["end_lap"] + 1:
                current["end_lap"] = max(current["end_lap"], segment["end_lap"])
                continue

            if current is not None:
                status_segments.append(current)
            current = segment

        if current is not None:
            status_segments.append(current)

    if results is not None and "Position" in results.columns:
        results_ordered = results.sort_values(by="Position")
    elif results is not None and "Time" in results.columns:
        results_ordered = results.sort_values(by="Time", na_position="last")
    else:
        results_ordered = results
    leader_time = None
    if practice_mode:
        leader_time = driver_meta[0].get("result_time", "-") if driver_meta else None
        for index, row in enumerate(rows, start=1):
            row["current_position"] = index
    elif results_ordered is not None and not results_ordered.empty:
        leader_row = results_ordered.iloc[0]
        leader_time = format_session_time(leader_row.get("Time", ""))
        leader_laps = int(float(leader_row.get("Laps", 0) or 0))
        for row in rows:
            driver_row = results_ordered[results_ordered["DriverNumber"].astype(str).str.strip() == str(row["driver_number"]).strip()]
            if driver_row.empty:
                row["time_label"] = "-"
                continue
            result_row = driver_row.iloc[0]
            if row["current_position"] == 1:
                row["time_label"] = leader_time
            else:
                row["time_label"] = classify_result_label(result_row, leader_laps)
    else:
        for row in rows:
            row["time_label"] = "-"

    ordered_rows = sorted(rows, key=lambda item: item["current_position"])
    return {
        "title": "Pit strategy",
        "lap_total": max_lap,
        "leader_time": leader_time,
        "drivers": ordered_rows,
        "status_segments": status_segments,
    }


def _qualifying_phases(session, driver_number, session_code):
    if not session or not driver_number or str(session_code).strip().upper() not in {"Q", "SQ"}:
        return None

    results = getattr(session, "results", None)
    if results is None or results.empty:
        return None

    row_match = results[results["DriverNumber"].astype(str).str.strip() == str(driver_number).strip()]
    if row_match.empty:
        return None

    row = row_match.iloc[0]
    driver_laps = _safe_driver_laps(session, driver_number)
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
                "is_personal_best": bool(lap_row.get("IsPersonalBest", False)) if pd.notna(lap_row.get("IsPersonalBest", False)) else False,
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
                "is_personal_best": False,
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


def _telemetry_charts(lap, gap_reference_lap=None):
    telemetry = lap.get_telemetry(frequency="original").reset_index(drop=True)
    if telemetry.empty or "Time" not in telemetry.columns:
        return []

    time_series = telemetry["Time"].dt.total_seconds()
    session = getattr(lap, "session", None)
    driver_ahead_lookup = _driver_abbreviation_lookup(session)
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
    gap_title = "Gap to Driver Ahead"
    specs = [
        ("Speed", "Speed", "km/h", None),
        ("Throttle", "Throttle", "%", None),
        ("Brake", "Brake", "0 / 1", lambda value: 1 if bool(value) else 0),
        ("RPM", "RPM", "", None),
        ("nGear", "Gear", "", None),
        ("DistanceToDriverAhead", gap_title, "s", None),
    ]

    charts = []
    gap_ahead_points = []
    driver_ahead_points = []
    using_reference_gap = False
    if gap_reference_lap is not None:
        try:
            reference_telemetry = gap_reference_lap.get_telemetry(frequency="original").reset_index(drop=True)
        except Exception:
            reference_telemetry = None
        if (
            reference_telemetry is not None
            and not reference_telemetry.empty
            and "Time" in reference_telemetry.columns
            and "Distance" in reference_telemetry.columns
            and "Distance" in telemetry.columns
        ):
            current_gap_frame = telemetry.loc[:, ["Time", "Distance"]].dropna(subset=["Time", "Distance"]).copy()
            reference_gap_frame = reference_telemetry.loc[:, ["Time", "Distance"]].dropna(subset=["Time", "Distance"]).copy()
            if not current_gap_frame.empty and not reference_gap_frame.empty:
                reference_gap_frame = reference_gap_frame.sort_values(by="Distance")
                reference_samples = []
                for _, ref_row in reference_gap_frame.iterrows():
                    ref_distance = ref_row.get("Distance")
                    ref_time = _lap_time_seconds(ref_row.get("Time", None))
                    if ref_distance is None or pd.isna(ref_distance) or ref_time is None:
                        continue
                    reference_samples.append((float(ref_distance), float(ref_time)))

                def interpolate_distance(samples, target_distance):
                    if not samples:
                        return None
                    if len(samples) == 1:
                        return samples[0][1]
                    if target_distance <= samples[0][0]:
                        return samples[0][1]
                    if target_distance >= samples[-1][0]:
                        return samples[-1][1]

                    for index in range(1, len(samples)):
                        prev_distance, prev_time = samples[index - 1]
                        next_distance, next_time = samples[index]
                        if target_distance > next_distance:
                            continue

                        span = next_distance - prev_distance or 1.0
                        ratio = (target_distance - prev_distance) / span
                        return prev_time + (next_time - prev_time) * ratio

                    return samples[-1][1]

                if reference_samples:
                    for t_value, distance_value in zip(current_gap_frame["Time"].dt.total_seconds(), current_gap_frame["Distance"]):
                        if pd.isna(t_value) or pd.isna(distance_value):
                            continue
                        reference_time = interpolate_distance(reference_samples, float(distance_value))
                        if reference_time is None:
                            continue
                        gap_ahead_points.append([float(t_value), float(t_value) - float(reference_time)])
                    using_reference_gap = bool(gap_ahead_points)

    if not gap_ahead_points and "DistanceToDriverAhead" in telemetry.columns and "Speed" in telemetry.columns:
        for t_value, distance_value, speed_value in zip(time_series, telemetry["DistanceToDriverAhead"], telemetry["Speed"]):
            gap_seconds = _gap_ahead_seconds(distance_value, speed_value)
            if pd.isna(t_value) or gap_seconds is None:
                continue
            gap_ahead_points.append([float(t_value), float(gap_seconds)])
        if "DriverAhead" in telemetry.columns:
            for t_value, driver_ahead_value in zip(time_series, telemetry["DriverAhead"]):
                if pd.isna(t_value) or pd.isna(driver_ahead_value):
                    continue
                driver_number = _normalize_driver_number(driver_ahead_value)
                if not driver_number:
                    continue
                try:
                    driver_ahead_points.append([float(t_value), float(driver_number)])
                except (TypeError, ValueError):
                    continue

    for index, (column, title, unit, transform) in enumerate(specs):
        if column == "DistanceToDriverAhead":
            points = gap_ahead_points
            if not points:
                continue
        elif column not in telemetry.columns:
            continue

        if column != "DistanceToDriverAhead":
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
            }
        )
        if column == "DistanceToDriverAhead":
            if using_reference_gap:
                charts[-1]["title"] = "Gap to Fastest Lap"
                charts[-1]["reference_mode"] = "qualifying_fastest_lap"
            else:
                charts[-1]["driver_ahead_points"] = driver_ahead_points
                charts[-1]["driver_ahead_lookup"] = driver_ahead_lookup

    return charts


def _race_position_graph(session):
    if not session:
        return None

    results = getattr(session, "results", None)
    if results is None or results.empty:
        return None

    if "Position" not in results.columns:
        return None

    driver_meta = _driver_options(session, results=results)
    if not driver_meta:
        return None

    def _starting_position(result_row, fallback_position):
        for column in ("Grid", "GridPosition", "StartingGridPosition", "StartPosition"):
            if column in result_row.index:
                value = result_row.get(column)
                if pd.notna(value):
                    try:
                        start_position = int(float(value))
                        if start_position > 0:
                            return start_position
                    except (TypeError, ValueError):
                        pass
        try:
            fallback_position = int(float(fallback_position))
            if fallback_position > 0:
                return fallback_position
        except (TypeError, ValueError):
            pass
        return None

    driver_results = {}
    max_lap = 0
    position_series = pd.to_numeric(results["Position"], errors="coerce").dropna()
    max_position = int(position_series.max()) if not position_series.empty else 0
    if max_position <= 0:
        max_position = len(driver_meta)

    for driver in driver_meta:
        driver_number = driver["value"]
        laps = _safe_driver_laps(session, driver_number)
        if laps is None or laps.empty or "Position" not in laps.columns:
            continue

        ordered = laps[laps["LapNumber"].notna() & laps["Position"].notna()].copy()
        if ordered.empty:
            continue
        ordered = ordered.sort_values(by=["LapNumber", "Time"])

        result_row = results[results["DriverNumber"].astype(str).str.strip() == str(driver_number).strip()]
        start_position = None
        if not result_row.empty:
            result_row = result_row.iloc[0]
            start_position = _starting_position(result_row, result_row.get("Position"))

        points = []
        if start_position is not None:
            points.append([0, start_position])
            if start_position > max_position:
                max_position = start_position
        for _, lap_row in ordered.iterrows():
            lap_number = lap_row.get("LapNumber")
            position = lap_row.get("Position")
            if pd.isna(lap_number) or pd.isna(position):
                continue
            try:
                lap_number = int(lap_number)
                position = int(float(position))
            except (TypeError, ValueError):
                continue
            points.append([lap_number, position])
            if lap_number > max_lap:
                max_lap = lap_number

        if not points:
            continue

        driver_results[driver_number] = {
            "value": driver_number,
            "driver": driver["full_name"],
            "abbreviation": driver["abbreviation"],
            "team": driver["team_name"],
            "color": _team_color(driver["team_name"]),
            "points": points,
            "current_position": points[-1][1],
        }

    if not driver_results or max_lap <= 0:
        return None

    def classify_track_status(raw_status):
        status = _clean_value(raw_status).strip()
        if not status:
            return None
        if "5" in status:
            return {"label": "Red Flag", "short": "RF", "color": "#ef4444"}
        if "4" in status:
            return {"label": "Safety Car", "short": "SC", "color": "#f97316"}
        if "6" in status or "7" in status:
            return {"label": "Virtual Safety Car", "short": "VSC", "color": "#38bdf8"}
        if "2" in status:
            return {"label": "Yellow Flag", "short": "YF", "color": "#facc15"}
        return None

    status_segments = []
    track_status = getattr(session, "track_status", None)
    laps_for_status = None
    session_laps = _safe_session_laps(session)
    if session_laps is not None and "LapNumber" in session_laps.columns and "LapStartTime" in session_laps.columns and "Time" in session_laps.columns:
        laps_for_status = session_laps[
            session_laps["LapNumber"].notna() & session_laps["LapStartTime"].notna() & session_laps["Time"].notna()
        ].copy()
        if not laps_for_status.empty:
            laps_for_status = laps_for_status.sort_values(by=["LapNumber", "Time"])

    if track_status is not None and not track_status.empty and laps_for_status is not None and not laps_for_status.empty:
        status_events = track_status.sort_values(by="Time").copy()
        status_events["end_time"] = status_events["Time"].shift(-1)
        final_lap_time = laps_for_status["Time"].max()

        current = None
        for _, row in status_events.iterrows():
            marker = classify_track_status(row.get("Status"))
            if marker is None or marker["label"] not in {"Safety Car", "Virtual Safety Car", "Red Flag"}:
                continue

            start_time = row.get("Time")
            end_time = row.get("end_time")
            if pd.isna(start_time):
                continue
            if pd.isna(end_time):
                end_time = final_lap_time
            if pd.isna(end_time) or end_time <= start_time:
                continue

            overlapping_laps = laps_for_status[
                (laps_for_status["LapStartTime"] < end_time) & (laps_for_status["Time"] > start_time)
            ]["LapNumber"].dropna()
            if overlapping_laps.empty:
                continue

            segment = {
                "start_lap": int(float(overlapping_laps.min())),
                "end_lap": int(float(overlapping_laps.max())),
                **marker,
            }

            if current is not None and current["label"] == segment["label"] and segment["start_lap"] <= current["end_lap"] + 1:
                current["end_lap"] = max(current["end_lap"], segment["end_lap"])
                continue

            if current is not None:
                status_segments.append(current)
            current = segment

        if current is not None:
            status_segments.append(current)

    ordered_series = sorted(driver_results.values(), key=lambda item: item["current_position"])

    return {
        "title": "Race position changes",
        "lap_total": max_lap,
        "max_position": max_position,
        "series": ordered_series,
        "status_segments": status_segments,
    }


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

    try:
        lap_pos = lap.get_pos_data()
    except Exception:
        lap_pos = None

    racing_frame = None
    if lap_pos is not None and not lap_pos.empty:
        racing_frame = lap_pos.loc[:, [column for column in ["Time", "X", "Y"] if column in lap_pos.columns]].dropna(subset=["X", "Y"]).copy()

    if (racing_frame is None or racing_frame.empty) and telemetry is not None and not telemetry.empty:
        telemetry_frame = telemetry.loc[:, [column for column in ["Time", "X", "Y"] if column in telemetry.columns]].dropna(subset=["X", "Y"]).copy()
        if not telemetry_frame.empty:
            racing_frame = telemetry_frame

    if racing_frame is None or racing_frame.empty:
        return None

    if circuit_info is not None:
        try:
            circuit_points = circuit_info.corners.loc[:, ["X", "Y"]].dropna().to_numpy().tolist()
        except Exception:
            circuit_points = []
    else:
        circuit_points = []

    outline = circuit_points or racing_frame.loc[:, ["X", "Y"]].to_numpy().tolist()
    racing = racing_frame.loc[:, ["X", "Y"]].to_numpy().tolist()
    rotation = circuit_info.rotation if circuit_info is not None else 0.0
    if circuit_info is not None:
        outline = _rotate_xy(outline, rotation)
        racing = _rotate_xy(racing, rotation)

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

    speed_profile = []
    if telemetry is not None and not telemetry.empty and "Time" in telemetry.columns and "Speed" in telemetry.columns:
        speed_frame = telemetry.loc[:, ["Time", "Speed"]].dropna(subset=["Time", "Speed"]).copy()
        if not speed_frame.empty:
            speed_frame = speed_frame.sort_values(by="Time")
            for _, speed_row in speed_frame.iterrows():
                speed_seconds = _lap_time_seconds(speed_row.get("Time", None))
                if speed_seconds is None:
                    continue
                try:
                    speed_value = float(speed_row.get("Speed", None))
                except (TypeError, ValueError):
                    continue
                speed_profile.append((float(speed_seconds), speed_value))

    def _interpolate_series_value(samples, target_seconds):
        if not samples:
            return None
        if len(samples) == 1:
            return samples[0][1]
        if target_seconds <= samples[0][0]:
            return samples[0][1]
        if target_seconds >= samples[-1][0]:
            return samples[-1][1]

        for index in range(1, len(samples)):
            prev_time, prev_value = samples[index - 1]
            next_time, next_value = samples[index]
            if target_seconds > next_time:
                continue

            span = next_time - prev_time or 1.0
            ratio = (target_seconds - prev_time) / span
            return prev_value + (next_value - prev_value) * ratio

        return samples[-1][1]

    racing_samples = []
    for index, point in enumerate(racing):
        if index >= len(time_series):
            break
        sample_time = float(time_series[index])
        sample_speed = _interpolate_series_value(speed_profile, sample_time)
        racing_samples.append(
            {
                "x": float(point[0]),
                "y": float(point[1]),
                "t": sample_time,
                "speed": float(sample_speed) if sample_speed is not None else None,
            }
        )

    acceleration_values = []
    for index, sample in enumerate(racing_samples):
        current_speed = sample.get("speed")
        current_time = sample.get("t")
        if current_speed is None or current_time is None:
            sample["acceleration"] = None
            continue

        previous_sample = None
        for previous_index in range(index - 1, -1, -1):
            candidate = racing_samples[previous_index]
            if candidate.get("speed") is not None and candidate.get("t") is not None:
                previous_sample = candidate
                break

        next_sample = None
        for next_index in range(index + 1, len(racing_samples)):
            candidate = racing_samples[next_index]
            if candidate.get("speed") is not None and candidate.get("t") is not None:
                next_sample = candidate
                break

        acceleration = None
        if previous_sample is not None and next_sample is not None:
            dt = float(next_sample["t"]) - float(previous_sample["t"])
            if dt > 0:
                acceleration = (((float(next_sample["speed"]) - float(previous_sample["speed"])) / 3.6) / dt)
        elif next_sample is not None:
            dt = float(next_sample["t"]) - float(current_time)
            if dt > 0:
                acceleration = (((float(next_sample["speed"]) - float(current_speed)) / 3.6) / dt)
        elif previous_sample is not None:
            dt = float(current_time) - float(previous_sample["t"])
            if dt > 0:
                acceleration = (((float(current_speed) - float(previous_sample["speed"])) / 3.6) / dt)

        sample["acceleration"] = float(acceleration) if acceleration is not None else None
        if sample["acceleration"] is not None:
            acceleration_values.append(sample["acceleration"])

    speed_values = [sample.get("speed") for sample in racing_samples if sample.get("speed") is not None]
    speed_min = min(speed_values) if speed_values else None
    speed_max = max(speed_values) if speed_values else None
    acceleration_min = min(acceleration_values) if acceleration_values else None
    acceleration_max = max(acceleration_values) if acceleration_values else None
    acceleration_limit = None
    if acceleration_values:
        acceleration_magnitudes = pd.Series([abs(value) for value in acceleration_values], dtype="float64")
        acceleration_limit = float(acceleration_magnitudes.quantile(0.9))
        if pd.isna(acceleration_limit) or acceleration_limit <= 0:
            acceleration_limit = max(abs(value) for value in acceleration_values)

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
                "time": _format_sector_time(sector_value),
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
    padding = max((max(xs) - min(xs)) * 0.03, (max(ys) - min(ys)) * 0.03, 16)

    return {
        "outline": outline,
        "racing": racing,
        "samples": racing_samples,
        "sector_markers": sector_markers,
        "max_speed_marker": max_speed_marker,
        "min_speed_marker": min_speed_marker,
        "speed_min": float(speed_min) if speed_min is not None else None,
        "speed_max": float(speed_max) if speed_max is not None else None,
        "acceleration_min": float(acceleration_min) if acceleration_min is not None else None,
        "acceleration_max": float(acceleration_max) if acceleration_max is not None else None,
        "acceleration_limit": float(acceleration_limit) if acceleration_limit is not None else None,
        "bounds": {
            "min_x": min(xs) - padding,
            "max_x": max(xs) + padding,
            "min_y": min(ys) - padding,
            "max_y": max(ys) + padding,
        },
        "rotation": rotation,
        "duration": float(lap_duration_seconds) if lap_duration_seconds is not None else None,
    }


def _qualifying_overview_track_map_payload(session, phase=None, driver_options=None, phase_windows=None):
    if not session:
        return None

    phase_code = str(phase or "Q1").strip().upper()
    if phase_code not in {"Q1", "Q2", "Q3", "ALL"}:
        phase_code = "Q1"

    session_laps = _safe_session_laps(session)
    if session_laps is None or getattr(session_laps, "empty", True):
        return None

    qualifying_laps = session_laps
    if "LapTime" in qualifying_laps.columns:
        qualifying_laps = qualifying_laps[qualifying_laps["LapTime"].notna()]

    if qualifying_laps is None or getattr(qualifying_laps, "empty", True):
        return None

    if phase_code in {"Q1", "Q2", "Q3"}:
        phase_start = None
        phase_end = None
        if isinstance(phase_windows, dict):
            phase_window = phase_windows.get(phase_code, {}) or {}
            phase_start = phase_window.get("start")
            phase_end = phase_window.get("end")
        if phase_start is None or phase_end is None:
            try:
                fallback_graph = _qualifying_timeline_graph(session, phase_code, split_sections=False)
            except Exception:
                fallback_graph = None
            if fallback_graph and isinstance(fallback_graph.get("phase_windows"), dict):
                phase_window = fallback_graph.get("phase_windows", {}).get(phase_code, {}) or {}
                phase_start = phase_window.get("start")
                phase_end = phase_window.get("end")

        if phase_start is not None and phase_end is not None:
            try:
                phase_start = float(phase_start)
                phase_end = float(phase_end)
            except (TypeError, ValueError):
                phase_start = None
                phase_end = None

        if phase_start is not None and phase_end is not None:
            filtered_indices = []
            for index, lap in qualifying_laps.iterrows():
                lap_start = _lap_time_seconds(lap.get("LapStartTime", None))
                lap_end = _lap_time_seconds(lap.get("Time", None))
                if lap_start is None and lap_end is None:
                    continue
                if lap_start is None:
                    lap_start = lap_end
                if lap_end is None:
                    lap_end = lap_start
                if lap_start < phase_end and lap_end > phase_start:
                    filtered_indices.append(index)

            if filtered_indices:
                qualifying_laps = qualifying_laps.loc[filtered_indices]
            else:
                return None

    if qualifying_laps is None or getattr(qualifying_laps, "empty", True):
        return None

    reference_index = None
    if "LapTime" in qualifying_laps.columns:
        try:
            valid_lap_times = qualifying_laps["LapTime"].dropna()
            if not valid_lap_times.empty:
                reference_index = valid_lap_times.idxmin()
        except Exception:
            reference_index = None

    if reference_index is None:
        try:
            sorted_laps = qualifying_laps.sort_values(by=["LapTime", "Time"], na_position="last")
            if not sorted_laps.empty:
                reference_index = sorted_laps.index[0]
        except Exception:
            reference_index = None

    if reference_index is None:
        return None

    try:
        reference_lap = session_laps.loc[reference_index]
    except Exception:
        try:
            reference_lap = qualifying_laps.loc[reference_index]
        except Exception:
            reference_lap = None

    if reference_lap is None:
        return None
    if isinstance(reference_lap, pd.DataFrame):
        reference_lap = reference_lap.iloc[0] if not reference_lap.empty else None
    if reference_lap is None:
        return None

    reference_duration = _lap_time_seconds(reference_lap.get("LapTime", None))
    payload = None
    try:
        payload = _track_map_payload(session, reference_lap, reference_duration)
    except Exception:
        payload = None

    if payload is None:
        try:
            reference_telemetry = reference_lap.get_telemetry(frequency="original")
        except Exception:
            reference_telemetry = None
        try:
            payload = _track_map_payload(session, reference_lap, reference_duration, reference_telemetry)
        except Exception:
            payload = None

    if payload is None:
        return None

    sector_columns = [("Sector1Time", "S1"), ("Sector2Time", "S2"), ("Sector3Time", "S3")]
    sector_markers = []
    cumulative_seconds = 0.0
    for sector_column, sector_label in sector_columns:
        if sector_column not in qualifying_laps.columns:
            continue

        best_seconds = None
        best_lap = None
        for _, lap in qualifying_laps.iterrows():
            sector_seconds = _lap_time_seconds(lap.get(sector_column, None))
            if sector_seconds is None:
                continue
            if best_seconds is None or sector_seconds < best_seconds:
                best_seconds = float(sector_seconds)
                best_lap = lap

        if best_seconds is None:
            continue

        cumulative_seconds += float(best_seconds)
        marker = {
            "label": sector_label,
            "time": _format_sector_time(best_seconds),
            "seconds": float(best_seconds),
            "cumulative_seconds": float(cumulative_seconds),
        }
        if best_lap is not None:
            marker["source_lap_number"] = _clean_value(best_lap.get("LapNumber", "")).strip()
            marker["source_driver_number"] = _normalize_driver_number(best_lap.get("DriverNumber", ""))
        sector_markers.append(marker)

    reference_driver_number = _normalize_driver_number(reference_lap.get("DriverNumber", ""))
    reference_option = None
    if driver_options:
        reference_option = next(
            (
                option
                for option in driver_options
                if _normalize_driver_number(option.get("value", "")) == reference_driver_number
            ),
            None,
        )

    driver_abbr = _clean_value((reference_option or {}).get("abbreviation", "")).strip()
    if not driver_abbr:
        driver_abbr = _clean_value(reference_lap.get("Driver", "")).strip()
    if not driver_abbr:
        driver_abbr = reference_driver_number or "BEST"

    driver_color = _clean_value((reference_option or {}).get("team_badge_color", "")).strip()
    if not driver_color:
        team_name = _clean_value(reference_lap.get("Team", "")).strip()
        driver_color = _team_color(team_name) if team_name else "#44c2ff"

    payload["title"] = f"{phase_code} fastest sectors" if phase_code != "ALL" else "Qualifying fastest sectors"
    payload["phase_code"] = phase_code
    payload["driver_abbr"] = driver_abbr
    payload["driver_color"] = driver_color
    payload["sector_markers"] = sector_markers
    payload["reference_driver_number"] = reference_driver_number
    payload["reference_driver_name"] = _clean_value(reference_lap.get("Driver", "")).strip()

    return payload


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

    key = _session_key(year_int, gp, session_code)
    with _SESSION_LOAD_LOCK:
        state = _SESSION_LOAD_STATE.get(key)

    schedule_error = None
    selected_event = None
    event_options = _available_event_options(year_int)
    session_options = _all_session_options()
    session_badge = f"{year} {gp} - {_session_selection_label(session_code)}"
    session_title = f"{year} {gp}"

    if state is not None:
        if state.get("status") == "ready":
            year = str(state.get("year", year_int))
            gp = str(state.get("gp", gp))
            session_code = str(state.get("session_code", session_code)).strip().upper()
            event_options = state.get("event_options", event_options)
            session_options = state.get("session_options", session_options)
            session_badge = state.get("session_badge", session_badge)
            session_title = state.get("session_title", session_title)
        elif state.get("status") == "error":
            schedule_error = state.get("message")

    return {
        "year": year,
        "year_int": year_int,
        "gp": gp,
        "session_code": session_code,
        "schedule": None,
        "schedule_error": schedule_error,
        "selected_event": selected_event,
        "event_options": event_options,
        "session_options": session_options,
        "session_badge": session_badge,
        "session_title": session_title,
    }


def _session_key(year, gp, session_code):
    return (int(year), str(gp).strip(), str(session_code).strip().upper())


def _session_event_key(year, gp):
    return (int(year), str(gp).strip().casefold())


def _year_options():
    return [str(year) for year in range(YEAR_MAX, YEAR_MIN - 1, -1)]


def _schedule_for_year(year):
    return fastf1.get_event_schedule(int(year), include_testing=False)


def _event_options(schedule):
    if schedule is None or schedule.empty:
        return []

    return [
        {
            "value": str(event.EventName),
            "label": _event_display_label(event.Country, event.Location, event.EventName, int(index) + 1),
        }
        for index, (_, event) in enumerate(schedule.iterrows())
    ]


def _event_race_datetime(event):
    if event is None:
        return None

    for field_name in ("Session1", "Session2", "Session3", "Session4", "Session5"):
        if str(event.get(field_name, "")).strip().lower() != "race":
            continue

        for date_field in (f"{field_name}DateUtc", f"{field_name}Date"):
            value = event.get(date_field, None)
            if pd.isna(value) or not value:
                continue
            try:
                timestamp = pd.Timestamp(value)
            except Exception:
                continue
            if timestamp.tzinfo is not None:
                timestamp = timestamp.tz_convert("UTC")
            return timestamp.to_pydatetime().replace(tzinfo=None)
    return None


def _available_event_options(year):
    schedule = _schedule_for_year(year)
    if schedule is None or schedule.empty:
        return []

    now = datetime.utcnow()
    options = []
    for index, (_, event) in enumerate(schedule.iterrows()):
        race_dt = _event_race_datetime(event)
        if race_dt is None:
            continue
        if race_dt <= now:
            options.append(
                {
                    "value": str(event.EventName),
                    "label": _event_display_label(event.Country, event.Location, event.EventName, index + 1),
                }
            )
    return options


def _event_display_label(country, location, event_name, ordinal):
    name = str(event_name).strip()
    country_name = str(country).strip()
    location_name = str(location).strip()
    if country_name.lower() in {"usa", "united states", "united states of america"} and location_name and location_name.lower() != "nan":
        label = location_name
    elif country_name and country_name.lower() != "nan":
        label = country_name
    else:
        label = re.sub(r"\s*Grand Prix\s*$", "", name, flags=re.IGNORECASE).strip()
        label = re.sub(r"\s*Grand Prix\b", "", label, flags=re.IGNORECASE).strip() or name
    return f"{int(ordinal)}. {label}"


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

        code = _session_code_from_name(session_name)
        if code is None:
            continue

        session_date = event.get(f"{field_name}DateUtc", None)
        if pd.isna(session_date) or not session_date:
            session_date = event.get(f"{field_name}Date", None)

        options.append({"value": code, "label": session_name})

    return options


def _all_session_options():
    return [{"value": code, "label": label} for code, label in SESSION_CHOICES]


def _session_selector_context():
    year = request.args.get("year", "2024")
    gp = request.args.get("gp", "Bahrain Grand Prix")
    session_code = request.args.get("session", "R")

    try:
        year_int = int(year)
    except ValueError:
        year_int = YEAR_MAX

    year_int = max(YEAR_MIN, min(YEAR_MAX, year_int))
    year = str(year_int)

    schedule = _schedule_for_year(year_int)

    selected_event = _selected_event(schedule, gp) if schedule is not None else None
    event_options = _available_event_options(year_int)
    session_options = _session_options(selected_event) if selected_event is not None else _all_session_options()

    if selected_event is not None:
        gp = str(selected_event.EventName)
        if not session_options:
            session_code = ""

    session_badge = f"{year} {gp} - {_session_selection_label(session_code)}"
    if selected_event is not None:
        session_badge = f"{year} {selected_event.EventName} - {_session_selection_label(session_code)}"

    session_title = f"{year} {gp}"
    if selected_event is not None:
        session_title = f"{year} {selected_event.EventName}"

    return {
        "year": year,
        "year_int": year_int,
        "gp": gp,
        "session_code": session_code,
        "schedule": schedule,
        "schedule_error": None,
        "selected_event": selected_event,
        "event_options": event_options,
        "session_options": session_options,
        "session_badge": session_badge,
        "session_title": session_title,
    }


@app.route("/available-events")
def available_events():
    year = request.args.get("year", str(YEAR_MAX))
    try:
        year_int = int(year)
    except ValueError:
        year_int = YEAR_MAX
    year_int = max(YEAR_MIN, min(YEAR_MAX, year_int))
    return jsonify({
        "year": year_int,
        "events": _available_event_options(year_int),
    })


def _event_from_session_data(year, gp):
    schedule = _schedule_for_year(year)
    event = _selected_event(schedule, gp)
    return schedule, event


def _session_view_base_context(ctx, session_state):
    data = session_state if session_state.get("status") == "ready" else None
    loading = session_state.get("status") == "loading"
    loading_progress = session_state.get("progress", 0) if loading else 0
    loading_stage = session_state.get("stage", "") if loading else ""
    error = session_state.get("message") if session_state.get("status") == "error" else None
    if ctx["schedule_error"] and not error:
        error = ctx["schedule_error"]
    if not data and not loading and not error:
        error = "Session not available for this selection."
    session_badge = ctx["session_badge"]
    if data:
        session_badge = f"{data['year']} {data['event_name']} - {data['session_name']}"
    return {
        "data": data,
        "error": error,
        "loading": loading,
        "loading_progress": loading_progress,
        "loading_stage": loading_stage,
        "session_badge": session_badge,
        "session_title": ctx["session_title"],
        "years": _year_options(),
        "events": ctx["event_options"],
        "session_options": ctx["session_options"],
    }


def _load_session_data(year, gp, session_code):
    schedule, event = _event_from_session_data(year, gp)
    if event is None:
        raise ValueError(f"No event schedule found for {year}")

    session = event.get_session(session_code)
    session_code = str(session_code).strip().upper()
    load_laps = True
    if not _load_session_with_timeout(session, load_laps=load_laps, load_telemetry=True):
        raise TimeoutError(f"Timed out loading session data for {year} {gp} {session_code}")
    rows = _session_rows(session)
    race_position_graph = None
    pit_strategy_graph = None
    if str(session_code).strip().upper() == "R":
        try:
            race_position_graph = _race_position_graph(session)
        except Exception:
            race_position_graph = None
    if _supports_pit_strategy_graph(session_code):
        try:
            pit_strategy_graph = _pit_strategy_graph(session)
        except Exception:
            pit_strategy_graph = None
    stint_cache = _build_session_stint_cache(session, session_code)
    return {
        "status": "ready",
        "year": int(year),
        "gp": str(event.EventName),
        "session_name": session.name,
        "session_date": session.date.strftime("%Y-%m-%d %H:%M") if session.date else "-",
        "event_name": getattr(session.event, "EventName", gp),
        "event_country": getattr(session.event, "Country", "-"),
        "event_round": getattr(session.event, "RoundNumber", "-"),
        "event_options": _event_options(schedule),
        "session_options": _session_options(event),
        "session_badge": f"{int(year)} {getattr(session.event, 'EventName', gp)} - {_session_selection_label(session_code)}",
        "session_title": f"{int(year)} {getattr(session.event, 'EventName', gp)}",
        "session": session,
        "rows": rows,
        "race_position_graph": race_position_graph,
        "pit_strategy_graph": pit_strategy_graph,
        "stint_cache": stint_cache,
    }


def _stint_cache_key(session_code, driver_number):
    return f"{str(session_code).strip().upper()}:{_normalize_driver_number(driver_number)}"


def _build_session_stint_cache(session, session_code):
    if not session or str(session_code).strip().upper() not in {"R", "S"}:
        return {}

    cache = {}
    results = getattr(session, "results", None)
    if results is None or results.empty or "DriverNumber" not in results.columns:
        return cache

    driver_numbers = [
        _normalize_driver_number(value)
        for value in results["DriverNumber"].dropna().astype(str).tolist()
    ]
    for driver_number in driver_numbers:
        if not driver_number or driver_number in cache:
            continue
        try:
            cache[driver_number] = _race_stints(session, driver_number, session_code)
        except Exception:
            cache[driver_number] = None
    return cache


def _start_session_load(year, gp, session_code):
    key = _session_key(year, gp, session_code)
    event_key = _session_event_key(year, gp)
    with _SESSION_LOAD_LOCK:
        global _SESSION_LOAD_ACTIVE_EVENT
        if _SESSION_LOAD_ACTIVE_EVENT != event_key:
            _SESSION_LOAD_STATE.clear()
            _SESSION_LOAD_ACTIVE_EVENT = event_key

        state = _SESSION_LOAD_STATE.get(key)
        if state and state.get("status") in {"loading", "ready"}:
            return state

        state = {
            "status": "loading",
            "progress": 5,
            "stage": "Resolving event",
            "started_at": time.monotonic(),
            "year": int(year),
            "gp": str(gp),
            "session_code": str(session_code).strip().upper(),
        }
        _SESSION_LOAD_STATE[key] = state

    def worker():
        try:
            result = _load_session_data(year, gp, session_code)
        except Exception as exc:
            result = {"status": "error", "message": str(exc)}

        with _SESSION_LOAD_LOCK:
            current = _SESSION_LOAD_STATE.get(key)
            if current is state:
                current.clear()
                current.update(result)
                if current.get("status") == "ready":
                    global _SESSION_LOAD_ACTIVE_EVENT
                    _SESSION_LOAD_ACTIVE_EVENT = _session_event_key(current.get("year", year), current.get("gp", gp))

    Thread(target=worker, daemon=True).start()
    return state


def _loading_state_snapshot(state):
    if not state or state.get("status") != "loading":
        return dict(state) if state else None

    snapshot = dict(state)
    started_at = snapshot.get("started_at")
    if started_at is None:
        return snapshot

    elapsed = max(0.0, time.monotonic() - float(started_at))
    if elapsed >= _SESSION_LOAD_TIMEOUT_SECONDS:
        snapshot["status"] = "error"
        snapshot["progress"] = 100
        snapshot["stage"] = "Session load timed out"
        snapshot["message"] = "Session loading took too long. Please try again."
        return snapshot

    estimated_progress = min(92, int(5 + (elapsed * 7.5)))
    snapshot["progress"] = max(int(snapshot.get("progress", 0)), estimated_progress)

    stage = _SESSION_LOAD_STAGES[0][1]
    for threshold, label in _SESSION_LOAD_STAGES:
        if snapshot["progress"] >= threshold:
            stage = label
    snapshot["stage"] = stage
    return snapshot


def _get_session_state(year, gp, session_code, start_if_missing=True):
    key = _session_key(year, gp, session_code)
    with _SESSION_LOAD_LOCK:
        state = _SESSION_LOAD_STATE.get(key)

    if state is None and start_if_missing:
        state = _start_session_load(year, gp, session_code)

    if state is None:
        return {"status": "loading", "progress": 0, "stage": "Loading"}

    return _loading_state_snapshot(state)


def _get_session_state_cached(year, gp, session_code):
    try:
        return _load_session_data(year, gp, session_code)
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


def _session_object(year, gp, session_code):
    key = _session_key(year, gp, session_code)
    with _SESSION_LOAD_LOCK:
        state = _SESSION_LOAD_STATE.get(key)
        session = state.get("session") if state and state.get("status") == "ready" else None
    if session is not None:
        return session

    schedule, event = _event_from_session_data(year, gp)
    try:
        if event is not None:
            session = event.get_session(session_code)
        else:
            session = fastf1.get_session(year, gp, session_code)
        load_laps = True
        if not _load_session_with_timeout(session, load_laps=load_laps, load_telemetry=True):
            return None
        return session
    except Exception:
        return None


def _redirect_to_results():
    return redirect(url_for("results", **dict(request.args)))


@app.route("/")
@app.route("/session")
def index():
    return _redirect_to_results()


@app.route("/session-status")
def session_status():
    ctx = _resolve_context()
    session_state = _get_session_state(ctx["year"], ctx["gp"], ctx["session_code"])
    return jsonify({
        "status": session_state.get("status", "loading"),
        "progress": session_state.get("progress", 0) if session_state.get("status") == "loading" else 100,
        "stage": session_state.get("stage", "Loading"),
    })


@app.route("/results")
def results():
    ctx = _resolve_context()
    session_state = _get_session_state(ctx["year"], ctx["gp"], ctx["session_code"])
    page_context = _session_view_base_context(ctx, session_state)
    data = page_context["data"]
    loading = page_context["loading"]
    loading_progress = page_context["loading_progress"]
    loading_stage = page_context["loading_stage"]
    error = page_context["error"]
    strategy = _strategy_summary(data) if data else None
    session_code = str(ctx["session_code"]).strip().upper()
    session_is_race = session_code == "R"
    session_is_qualifying = session_code in {"Q", "SQ"}
    session = data.get("session") if data and data.get("session") is not None else None
    if session is None and data and (session_is_race or session_is_qualifying):
        session = _session_object(ctx["year"], ctx["gp"], ctx["session_code"])
    if session is None and session_is_qualifying:
        try:
            session = fastf1.get_session(int(ctx["year"]), ctx["gp"], ctx["session_code"])
            session.load(laps=True, telemetry=False, weather=False, messages=True)
        except Exception:
            session = None
    driver_number = request.args.get("driver", "").strip()
    qualifying_phase = request.args.get("phase", "Q1").strip().upper() if session_is_qualifying else ""
    if qualifying_phase not in {"Q1", "Q2", "Q3", "ALL"}:
        qualifying_phase = "Q1"
    qualifying_rows_phase = qualifying_phase if qualifying_phase in {"Q1", "Q2", "Q3"} else "Q1"
    race_position_graph = data.get("race_position_graph") if data else None
    pit_strategy_graph = data.get("pit_strategy_graph") if data else None
    if session_is_race and session:
        if race_position_graph is None:
            try:
                race_position_graph = _race_position_graph(session)
            except Exception:
                race_position_graph = None
        if pit_strategy_graph is None:
            try:
                pit_strategy_graph = _pit_strategy_graph(session)
            except Exception:
                pit_strategy_graph = None
    if not session_is_race and _supports_pit_strategy_graph(session_code) and session and pit_strategy_graph is None:
        try:
            pit_strategy_graph = _pit_strategy_graph(session)
        except Exception:
            pit_strategy_graph = None
    qualifying_results = _qualifying_driver_results(session, qualifying_rows_phase) if session_is_qualifying else None
    qualifying_phase_rows = _qualifying_phase_rows(session, qualifying_rows_phase) if session_is_qualifying else []
    qualifying_timeline_graph = _qualifying_timeline_graph(
        session,
        qualifying_phase,
        split_sections=(qualifying_phase != "ALL"),
    ) if session_is_qualifying else None
    qualifying_timeline_combined_graph = _qualifying_timeline_graph(session, qualifying_phase, split_sections=False) if session_is_qualifying else None
    driver_options = _driver_options(session, results=qualifying_results) if session else []
    driver_number = _resolve_driver(session, driver_number, driver_options) if session and driver_number else None
    selected_driver_data = next((option for option in driver_options if option["value"] == driver_number), None)
    qualifying_run_options = _qualifying_run_options(session, driver_number) if session_is_qualifying and session and driver_number else []
    qualifying_run_laps = _qualifying_run_laps(session, driver_number) if session_is_qualifying and session and driver_number else []
    qualifying_phase = qualifying_phase if qualifying_phase in {"Q1", "Q2", "Q3", "ALL"} else "Q1"
    if session_is_qualifying and qualifying_phase_rows:
        leader_seconds = None
        for row in qualifying_phase_rows:
            if str(row.get("position", "")).strip() == "1":
                leader_seconds = row.get("phase_seconds")
                break
        for row in qualifying_phase_rows:
            if str(row.get("position", "")).strip() == "1":
                row["display_time"] = _format_lap_time(row.get("phase_time"))
            else:
                phase_seconds = row.get("phase_seconds")
                if leader_seconds is not None and phase_seconds is not None:
                    gap = max(phase_seconds - leader_seconds, 0.0)
                    row["display_time"] = f"+{_format_lap_time(pd.to_timedelta(gap, unit='s'))}"
                else:
                    row["display_time"] = _format_lap_time(row.get("phase_time"))
    qualifying_overview_track_map = _qualifying_overview_track_map_payload(
        session,
        qualifying_phase,
        driver_options=driver_options,
        phase_windows=qualifying_timeline_combined_graph.get("phase_windows", {}) if isinstance(qualifying_timeline_combined_graph, dict) else None,
    ) if session_is_qualifying else None

    return render_template(
        "index.html",
        page="results",
        view="results",
        strategy=strategy,
        race_position_graph=race_position_graph,
        pit_strategy_graph=pit_strategy_graph,
        session_is_qualifying=session_is_qualifying,
        qualifying_phase=qualifying_phase,
        qualifying_phase_rows=qualifying_phase_rows,
        qualifying_timeline_graph=qualifying_timeline_graph,
        qualifying_timeline_combined_graph=qualifying_timeline_combined_graph,
        qualifying_overview_track_map=qualifying_overview_track_map,
        qualifying_run_options=qualifying_run_options,
        qualifying_run_laps=qualifying_run_laps,
        selected_driver=driver_number,
        selected_driver_data=selected_driver_data,
        **page_context,
        form_action="/results",
        form={
            "year": ctx["year"],
            "gp": ctx["gp"],
            "session": ctx["session_code"],
            "phase": qualifying_phase,
        },
    )


@app.route("/data")
@app.route("/lap")
def data():
    ctx = _resolve_context()
    session_state = _get_session_state(ctx["year"], ctx["gp"], ctx["session_code"])
    page_context = _session_view_base_context(ctx, session_state)
    data_state = page_context["data"]
    session = data_state.get("session") if data_state and data_state.get("session") is not None else None
    driver_number = request.args.get("driver", "").strip()
    driver_requested = bool(driver_number)
    lap_key = request.args.get("lap", "")
    stint_key = request.args.get("stint", "")
    qualifying_phase = request.args.get("phase", "").strip().upper()
    lap_requested = bool(lap_key)
    session_code = str(ctx["session_code"]).strip().upper()
    session_is_qualifying = session_code in {"Q", "SQ"}
    session_is_race = session_code == "R"
    lap_page_requested = request.path.rstrip("/").endswith("/lap")
    if session is None and (data_state or lap_requested or driver_requested or session_is_race or session_is_qualifying):
        session = _session_object(ctx["year"], ctx["gp"], ctx["session_code"])
    if session is None and session_is_qualifying:
        try:
            session = fastf1.get_session(int(ctx["year"]), ctx["gp"], ctx["session_code"])
            session.load(laps=True, telemetry=False, weather=False, messages=True)
        except Exception:
            session = None
    if session_is_qualifying:
        qualifying_phase = qualifying_phase if qualifying_phase in {"Q1", "Q2", "Q3"} else "Q1"
    else:
        qualifying_phase = ""

    if session and (lap_requested or (lap_page_requested and (driver_requested or stint_key))):
        try:
            session.load(laps=True, telemetry=True, weather=False, messages=True)
        except Exception:
            pass

    qualifying_results = _qualifying_driver_results(session, qualifying_phase) if session_is_qualifying else None
    qualifying_phase_rows = _qualifying_phase_rows(session, qualifying_phase) if session_is_qualifying else []
    qualifying_timeline_graph = _qualifying_timeline_graph(session, qualifying_phase, split_sections=True) if session_is_qualifying else None
    qualifying_timeline_combined_graph = _qualifying_timeline_graph(session, qualifying_phase, split_sections=False) if session_is_qualifying else None
    race_stints = None
    telemetry_columns = []
    telemetry_rows = []
    telemetry_summary = None
    lap_record = []
    telemetry_charts = []
    primary_telemetry_charts = []
    secondary_telemetry_charts = []
    qualifying_gap_reference_lap = None
    track_map = None
    race_position_graph = None
    pit_strategy_graph = None
    selected_stint = _parse_stint_value(stint_key)
    selected_lap_value = lap_key if lap_requested else ""
    selected_lap_data = None
    stint_cache = data_state.get("stint_cache", {}) if data_state else {}
    if session_is_race and session:
        try:
            race_position_graph = _race_position_graph(session)
        except Exception:
            race_position_graph = None
        try:
            pit_strategy_graph = _pit_strategy_graph(session)
        except Exception:
            pit_strategy_graph = None

    if session_is_race and session:
        driver_options = _driver_options(session, results=getattr(session, "results", None))
    else:
        driver_options = _driver_options(session, results=qualifying_results) if session else []
    driver_groups = _driver_groups(session, results=qualifying_results) if session else []

    driver_result_times = {}
    if pit_strategy_graph and pit_strategy_graph.get("drivers"):
        for driver in pit_strategy_graph["drivers"]:
            result_time = _clean_value(driver.get("time_label", "-")).strip()
            if not result_time or result_time == "-":
                continue
            driver_number_key = _clean_value(driver.get("driver_number", "")).strip()
            full_name_key = _clean_value(driver.get("driver", "")).strip()
            abbreviation_key = _clean_value(driver.get("abbreviation", "")).strip()
            if driver_number_key:
                driver_result_times[driver_number_key] = result_time
            if full_name_key:
                driver_result_times[full_name_key] = result_time
            if abbreviation_key:
                driver_result_times[abbreviation_key] = result_time
    elif data_state and not session_is_qualifying:
        for row in data_state.get("rows", []):
            result_time = _format_race_result_time(row.get("Time", "-"), row.get("Position", "-"))
            if result_time and result_time != "-":
                driver_result_times[_clean_value(row.get("FullName", "")).strip()] = result_time
                driver_result_times[_clean_value(row.get("Abbreviation", "")).strip()] = result_time

    if driver_requested:
        driver_number = _resolve_driver(session, driver_number, driver_options) if session else driver_number
    else:
        driver_number = None
    selected_driver_data = next((option for option in driver_options if option["value"] == driver_number), None)
    if selected_driver_data is None and driver_requested and driver_options:
        selected_driver_data = next((option for option in driver_options if option["value"] == request.args.get("driver", "").strip()), None)
    qualifying_run_options = _qualifying_run_options(session, driver_number) if session_is_qualifying and session and driver_number else []
    qualifying_run_laps = _qualifying_run_laps(session, driver_number) if session_is_qualifying and session and driver_number else []
    qualifying_run_value = request.args.get("run", "").strip()
    qualifying_phase_runs = list(qualifying_run_laps)
    qualifying_phase_other_runs = []
    selected_qualifying_run = None

    def qualifying_run_reference(run):
        return _clean_value(run.get("representative", "")).strip()

    if session_is_qualifying and qualifying_phase in {"Q1", "Q2", "Q3"} and qualifying_run_laps:
        phase_window = {}
        if qualifying_timeline_graph and isinstance(qualifying_timeline_graph.get("phase_windows"), dict):
            phase_window = qualifying_timeline_graph.get("phase_windows", {}).get(qualifying_phase, {}) or {}
        phase_run_refs = {
            _clean_value(run_ref).strip()
            for run_ref in (phase_window.get("runs") or [])
            if _clean_value(run_ref).strip()
        }
        if phase_run_refs:
            filtered_runs = [run for run in qualifying_run_laps if qualifying_run_reference(run) in phase_run_refs]
            if filtered_runs:
                qualifying_phase_runs = filtered_runs

    if session_is_qualifying and qualifying_phase_runs:
        if qualifying_run_value:
            selected_qualifying_run = next((run for run in qualifying_phase_runs if qualifying_run_reference(run) == qualifying_run_value), None)
            if selected_qualifying_run is None and qualifying_run_value.isdigit():
                selected_qualifying_run = next((run for run in qualifying_phase_runs if str(run.get("run_number")) == qualifying_run_value), None)
        if selected_qualifying_run is None and selected_lap_value:
            selected_qualifying_run = next((run for run in qualifying_phase_runs if any(lap.get("value") == selected_lap_value for lap in run.get("laps", []))), None)
        if selected_qualifying_run is None:
            fastest_runs = [run for run in qualifying_phase_runs if run.get("flying_time_seconds") is not None]
            selected_qualifying_run = min(
                fastest_runs,
                key=lambda run: float(run.get("flying_time_seconds")),
            ) if fastest_runs else qualifying_phase_runs[0]

    if session_is_qualifying and qualifying_phase_runs and selected_qualifying_run is not None:
        selected_run_reference = qualifying_run_reference(selected_qualifying_run)
        qualifying_phase_other_runs = [run for run in qualifying_phase_runs if qualifying_run_reference(run) != selected_run_reference]
    if session_is_qualifying and qualifying_phase_runs:
        for run in qualifying_phase_runs:
            laps = run.get("laps") or []
            run["lap_count"] = len(laps)
            run["compound"] = next((lap.get("compound") for lap in laps if lap.get("compound")), "-")
    driver_lap_options = _lap_options(session, driver_number) if session and driver_number else []
    if lap_page_requested and session_is_qualifying and driver_number and not selected_lap_value and selected_qualifying_run is not None:
        selected_lap_value = _clean_value(selected_qualifying_run.get("representative", "")).strip()
        if not selected_lap_value:
            selected_lap_value = next((lap.get("value") for lap in selected_qualifying_run.get("laps", []) if lap.get("value")), "")
        if selected_lap_value:
            selected_lap_data = _resolve_lap(session, driver_number, selected_lap_value)
    if session and driver_number:
        if session_is_qualifying:
            race_stints = _qualifying_phases(session, driver_number, ctx["session_code"])
        else:
            cache_key = _normalize_driver_number(driver_number)
            race_stints = stint_cache.get(cache_key) if cache_key in stint_cache else None
            if race_stints is None and cache_key:
                race_stints = _race_stints(session, driver_number, ctx["session_code"])
                stint_cache[cache_key] = race_stints
    if session and driver_number:
        if lap_requested:
            selected_lap_data = _resolve_lap(session, driver_number, lap_key)
            if selected_lap_data is not None:
                selected_lap_value = f"{_clean_value(driver_number)}:{_clean_value(selected_lap_data.get('LapNumber', ''))}"
                if selected_stint is None:
                    selected_stint = _parse_stint_value(selected_lap_data.get("Stint", None))
    if lap_page_requested and selected_stint is None and session and driver_number and race_stints and race_stints.get("stints"):
        selected_stint = _parse_stint_value(race_stints["stints"][0].get("stint", None))
    selected_stint_data = None
    if race_stints and race_stints.get("stints") and selected_stint is not None:
        selected_stint_data = next((item for item in race_stints["stints"] if item["stint"] == selected_stint), None)
    if lap_page_requested and not selected_lap_value and selected_stint_data is not None:
        # Default to a representative lap so /lap opens with populated telemetry.
        selected_lap_value = _default_stint_lap_value(selected_stint_data)
        if selected_lap_value:
            selected_lap_data = _resolve_lap(session, driver_number, selected_lap_value)
    race_stints_display = race_stints
    if session_is_race and race_stints and race_stints.get("stints") and driver_number:
        pit_stop_average_seconds = _race_pit_stop_average_seconds(stint_cache, exclude_driver_number=driver_number)
        if pit_stop_average_seconds is None and session and driver_options:
            selected_driver_key = _normalize_driver_number(driver_number)
            for driver_option in driver_options:
                other_driver_number = driver_option.get("value")
                other_driver_key = _normalize_driver_number(other_driver_number)
                if not other_driver_key or other_driver_key == selected_driver_key:
                    continue
                if other_driver_key in stint_cache and stint_cache.get(other_driver_key):
                    continue
                try:
                    stint_cache[other_driver_key] = _race_stints(session, other_driver_number, ctx["session_code"])
                except Exception:
                    stint_cache[other_driver_key] = None
            pit_stop_average_seconds = _race_pit_stop_average_seconds(stint_cache, exclude_driver_number=driver_number)
        if pit_stop_average_seconds is not None:
            pit_stop_average_time = _format_minutes_seconds(pit_stop_average_seconds)
            race_stints_display = {**race_stints}
            race_stints_display["stints"] = [dict(stint) for stint in race_stints.get("stints", [])]
            race_stints_display["pit_stop_average_seconds"] = pit_stop_average_seconds
            race_stints_display["pit_stop_average_time"] = pit_stop_average_time
            for stint in race_stints_display["stints"]:
                pit_stop_seconds = stint.get("pit_stop_seconds")
                if pit_stop_seconds is None:
                    continue
                try:
                    delta_seconds = float(pit_stop_seconds) - float(pit_stop_average_seconds)
                except (TypeError, ValueError):
                    continue
                delta_text = _format_signed_duration(delta_seconds)
                if abs(delta_seconds) < 0.0005:
                    comparison_class = "is-even-average"
                elif delta_seconds > 0:
                    comparison_class = "is-above-average"
                else:
                    comparison_class = "is-below-average"
                comparison_text = delta_text
                comparison_title = f"Average pit time {pit_stop_average_time} (AVG); gap {delta_text}."
                stint["pit_stop_average_time"] = pit_stop_average_time
                stint["pit_stop_delta_seconds"] = delta_seconds
                stint["pit_stop_delta_time"] = delta_text
                stint["pit_stop_comparison_class"] = comparison_class
                stint["pit_stop_comparison_text"] = comparison_text
                stint["pit_stop_comparison_title"] = comparison_title
    if selected_lap_data is not None:
        telemetry_columns, telemetry_rows, telemetry = _telemetry_rows(selected_lap_data)
        telemetry_summary = _lap_summary(selected_lap_data, telemetry)
        lap_record = _lap_record(selected_lap_data, telemetry)
        if session_is_qualifying:
            qualifying_gap_reference_lap = _qualifying_phase_fastest_lap(
                session,
                qualifying_phase,
                phase_rows=qualifying_phase_rows,
                phase_windows=qualifying_timeline_graph.get("phase_windows", {}) if isinstance(qualifying_timeline_graph, dict) else None,
            )
        telemetry_charts = _telemetry_charts(selected_lap_data, qualifying_gap_reference_lap)
        primary_telemetry_titles = {"Speed", "Throttle", "Brake"}
        primary_telemetry_charts = [
            chart
            for chart in telemetry_charts
            if chart.get("title") in primary_telemetry_titles
        ]
        secondary_telemetry_charts = [
            chart
            for chart in telemetry_charts
            if chart.get("title") not in primary_telemetry_titles
        ]
        try:
            lap_duration = _lap_time_seconds(selected_lap_data.get("LapTime", None))
            track_map = _track_map_payload(session, selected_lap_data, lap_duration, telemetry) if session else None
        except Exception:
            track_map = None
        app.logger.info(
            "data debug: lap_requested=%s driver=%s lap=%s selected_lap=%s telemetry_rows=%s track_map=%s",
            lap_requested,
            driver_number,
            lap_key,
            selected_lap_value,
            len(telemetry_rows),
            track_map is not None,
        )
    lap_entry_source = request.args.get("source", "").strip().lower()
    lap_back_driver_value = driver_number or request.args.get("driver", "").strip()
    lap_back_to_driver_table_url = url_for(
        "data",
        year=ctx["year"],
        gp=ctx["gp"],
        session=ctx["session_code"],
        **({"driver": lap_back_driver_value} if lap_back_driver_value else {}),
    )
    lap_back_to_overview_url = (
        url_for("results", year=ctx["year"], gp=ctx["gp"], session=ctx["session_code"])
        if lap_entry_source in {"pit-strategy", "race-position"}
        else ""
    )
    page_name = "lap" if request.path.rstrip("/").endswith("/lap") or selected_lap_data is not None else "data"
    form_action = "/lap" if session_code in {"R", "S"} else "/data"

    return render_template(
        "index.html",
        page=page_name,
        view="data",
        strategy=_strategy_summary(data_state) if data_state else None,
        data_summary=_data_summary(data_state) if data_state else None,
        telemetry_summary=telemetry_summary,
        lap_record=lap_record,
        selected_stint=selected_stint,
        selected_stint_data=selected_stint_data,
        telemetry_columns=telemetry_columns,
        telemetry_rows=telemetry_rows,
        telemetry_charts=telemetry_charts,
        primary_telemetry_charts=primary_telemetry_charts,
        secondary_telemetry_charts=secondary_telemetry_charts,
        race_position_graph=race_position_graph,
        pit_strategy_graph=pit_strategy_graph,
        track_map=track_map,
        qualifying_phase=qualifying_phase,
        qualifying_phase_rows=qualifying_phase_rows,
        qualifying_timeline_graph=qualifying_timeline_graph,
        qualifying_timeline_combined_graph=qualifying_timeline_combined_graph,
        qualifying_run_options=qualifying_run_options,
        qualifying_run_laps=qualifying_run_laps,
        qualifying_phase_runs=qualifying_phase_runs,
        qualifying_phase_other_runs=qualifying_phase_other_runs,
        selected_qualifying_run=selected_qualifying_run,
        form_action=form_action,
        form={
            "year": ctx["year"],
            "gp": ctx["gp"],
            "session": ctx["session_code"],
            "phase": qualifying_phase,
        },
        drivers=driver_options,
        driver_groups=driver_groups,
        driver_result_times=driver_result_times,
        selected_driver=driver_number,
        selected_driver_data=selected_driver_data,
        driver_lap_options=driver_lap_options,
        selected_lap=selected_lap_value,
        session_is_qualifying=session_is_qualifying,
        race_stints=race_stints_display,
        data=data_state,
        lap_back_to_driver_table_url=lap_back_to_driver_table_url,
        lap_back_to_overview_url=lap_back_to_overview_url,
        **{key: value for key, value in page_context.items() if key != "data"},
    )


@app.route("/strategy")
def strategy():
    ctx = _resolve_context()
    session_state = _get_session_state(ctx["year"], ctx["gp"], ctx["session_code"])
    page_context = _session_view_base_context(ctx, session_state)
    data = page_context["data"]
    strategy_data = _strategy_summary(data) if data else None

    return render_template(
        "index.html",
        page="strategy",
        view="strategy",
        strategy=strategy_data,
        form_action="/strategy",
        form={
            "year": ctx["year"],
            "gp": ctx["gp"],
            "session": ctx["session_code"],
        },
        **page_context,
    )


@app.errorhandler(404)
def not_found(error):
    return _render_error_page(
        404,
        "Page not found",
        "The page you requested does not exist in this session workspace.",
    )


@app.errorhandler(500)
def server_error(error):
    return _render_error_page(
        500,
        "Server error",
        "The app hit an unexpected problem while loading this view.",
    )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5001"))
    app.run(host="127.0.0.1", port=port, debug=False, use_reloader=False, threaded=True)
