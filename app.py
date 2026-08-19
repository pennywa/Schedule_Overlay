"""
Schedule Overlays — local multi-PDF schedule overlay calendar.
Phase 1: Environment & session-state foundation.
Phase 2: Four Seasons theme CSS injection.
Phase 3: Local pdfplumber schedule parser.
Phase 4: Weekly calendar grid + 12/24-hour toggle.
Phase 5: Saved schedules manager (max 10) + PDF export.
"""

from __future__ import annotations

import hashlib
import html
import io
import re
import uuid
from copy import deepcopy
from datetime import time as dt_time
from typing import Any

import pdfplumber
import streamlit as st
from reportlab.lib.colors import HexColor, black, white
from reportlab.lib.pagesizes import landscape, letter
from reportlab.pdfgen import canvas as pdf_canvas

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SCHEDULE_PALETTE: list[str] = [
    "#10B981",  # Emerald
    "#8B5CF6",  # Amethyst
    "#F59E0B",  # Amber
    "#3B82F6",  # Azure
    "#EC4899",  # Rose
    "#14B8A6",  # Teal
    "#F97316",  # Orange
    "#6366F1",  # Indigo
]

DAYS_SUN_FIRST: list[str] = [
    "Sunday",
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
]
DAYS_MON_FIRST: list[str] = [
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
]

SEASONS: list[str] = ["Winter", "Spring", "Summer", "Fall"]

DAY_ALIASES: dict[str, str] = {
    "sunday": "Sunday",
    "sun": "Sunday",
    "su": "Sunday",
    "monday": "Monday",
    "mon": "Monday",
    "tuesday": "Tuesday",
    "tue": "Tuesday",
    "tues": "Tuesday",
    "wednesday": "Wednesday",
    "wed": "Wednesday",
    "thursday": "Thursday",
    "thu": "Thursday",
    "thur": "Thursday",
    "thurs": "Thursday",
    "th": "Thursday",
    "friday": "Friday",
    "fri": "Friday",
    "saturday": "Saturday",
    "sat": "Saturday",
}

# Longer compact patterns first so MWF wins over MW / MF
COMPACT_DAY_PATTERNS: list[tuple[re.Pattern[str], list[str]]] = [
    (re.compile(r"\bM/?W/?F\b", re.I), ["Monday", "Wednesday", "Friday"]),
    (re.compile(r"\bT/?Th\b|\bTu/?Th\b|\bTTh\b|\bTR\b", re.I), ["Tuesday", "Thursday"]),
    (re.compile(r"\bM/?W\b", re.I), ["Monday", "Wednesday"]),
    (re.compile(r"\bM/?F\b", re.I), ["Monday", "Friday"]),
    (re.compile(r"\bW/?F\b", re.I), ["Wednesday", "Friday"]),
    (re.compile(r"\bSa/?Su\b", re.I), ["Saturday", "Sunday"]),
]

_TIME_TOKEN = r"(\d{1,2}(?::\d{2})?\s*(?:[AaPp][Mm])?)"
TIME_RANGE_RE = re.compile(
    _TIME_TOKEN + r"\s*(?:[-–—]|to)\s*" + _TIME_TOKEN,
    re.I,
)

DAY_LIST_RE = re.compile(
    r"\b("
    r"Mon(?:day)?|Tue(?:s(?:day)?)?|Wed(?:nesday)?|Thu(?:r(?:s(?:day)?)?)?|"
    r"Fri(?:day)?|Sat(?:urday)?|Sun(?:day)?|Th"
    r")"
    r"(?:\s*[/,&]\s*"
    r"(?:Mon(?:day)?|Tue(?:s(?:day)?)?|Wed(?:nesday)?|Thu(?:r(?:s(?:day)?)?)?|"
    r"Fri(?:day)?|Sat(?:urday)?|Sun(?:day)?|Th)"
    r")*\b",
    re.I,
)

# Weekly grid window: 7:00 through 22:00 (10 PM) hour labels
GRID_START_HOUR = 7
GRID_END_HOUR = 23  # exclusive upper bound → last label is 22:00
HOUR_HEIGHT_PX = 56
MAX_SAVED_SCHEDULES = 10

# CSS custom-property tokens per season (applied layout-wide via :root)
SEASON_THEMES: dict[str, dict[str, str]] = {
    "Winter": {
        "--bg-primary": "#F0F4F8",
        "--bg-secondary": "#E2E8F0",
        "--bg-surface": "#FFFFFF",
        "--bg-sidebar": "#1E3A5F",
        "--text-primary": "#0F172A",
        "--text-secondary": "#475569",
        "--text-inverse": "#F8FAFC",
        "--accent": "#38BDF8",
        "--accent-soft": "#BAE6FD",
        "--border": "#94A3B8",
        "--grid-line": "#CBD5E1",
        "--header-bg": "#1E3A5F",
        "--header-text": "#F8FAFC",
        "--conflict": "#DC2626",
        "--shadow": "rgba(15, 23, 42, 0.12)",
    },
    "Spring": {
        "--bg-primary": "#F7F5F0",
        "--bg-secondary": "#FCE8EF",
        "--bg-surface": "#FFFBFA",
        "--bg-sidebar": "#5C7A62",
        "--text-primary": "#1B2E1F",
        "--text-secondary": "#5C6B5E",
        "--text-inverse": "#FFF5F7",
        "--accent": "#E891A8",
        "--accent-soft": "#F8D7E0",
        "--border": "#E5B8C6",
        "--grid-line": "#E8D0D8",
        "--header-bg": "#6B8F71",
        "--header-text": "#FFF5F7",
        "--conflict": "#C62828",
        "--shadow": "rgba(232, 145, 168, 0.22)",
    },
    "Summer": {
        "--bg-primary": "#FFFBEB",
        "--bg-secondary": "#FEF3C7",
        "--bg-surface": "#FFFFFF",
        "--bg-sidebar": "#0284C7",
        "--text-primary": "#0C4A6E",
        "--text-secondary": "#0369A1",
        "--text-inverse": "#FFFFFF",
        "--accent": "#FBBF24",
        "--accent-soft": "#FDE68A",
        "--border": "#7DD3FC",
        "--grid-line": "#BAE6FD",
        "--header-bg": "#0284C7",
        "--header-text": "#FFFFFF",
        "--conflict": "#B91C1C",
        "--shadow": "rgba(2, 132, 199, 0.16)",
    },
    "Fall": {
        "--bg-primary": "#F5EDE4",
        "--bg-secondary": "#EAD9C8",
        "--bg-surface": "#FFFBF5",
        "--bg-sidebar": "#9A3412",
        "--text-primary": "#3B1F0E",
        "--text-secondary": "#7C4A2D",
        "--text-inverse": "#FFF7ED",
        "--accent": "#EA580C",
        "--accent-soft": "#FDBA74",
        "--border": "#D4A574",
        "--grid-line": "#E8D5C0",
        "--header-bg": "#9A3412",
        "--header-text": "#FFF7ED",
        "--conflict": "#991B1B",
        "--shadow": "rgba(154, 52, 18, 0.16)",
    },
}


# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------

def _empty_event(
    *,
    event_id: str = "",
    title: str = "",
    day: str = "Monday",
    start: str = "09:00",
    end: str = "10:00",
    schedule_id: str = "",
    color: str = "#10B981",
    source: str = "manual",
) -> dict[str, Any]:
    """Normalized event schema used across parse / CRUD / render."""
    return {
        "id": event_id,
        "title": title,
        "day": day,
        "start": start,
        "end": end,
        "schedule_id": schedule_id,
        "color": color,
        "source": source,
    }


def init_session_state() -> None:
    """Initialize all session keys once per browser session."""
    defaults: dict[str, Any] = {
        "calendar_title": "Fall 2026 Schedule",
        "week_start": "Monday",
        "start_day_pref": "Monday",  # alias kept in sync with week_start
        "time_format": "12-Hour",  # "12-Hour" | "24-Hour"
        "season": "Fall",
        "schedules": [],
        "events": [],
        "processed_files": set(),
        "edit_event_id": None,
        "parse_warnings": [],
        "next_color_index": 0,
        "saved_schedules": [],  # max MAX_SAVED_SCHEDULES snapshots
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value
    # Keep legacy week_start and start_day_pref aligned
    if st.session_state.get("start_day_pref") != st.session_state.get("week_start"):
        st.session_state.start_day_pref = st.session_state.week_start


def ordered_days() -> list[str]:
    """Return weekday column order based on start-day preference."""
    pref = st.session_state.get("start_day_pref") or st.session_state.get("week_start", "Monday")
    if pref == "Sunday":
        return DAYS_SUN_FIRST
    return DAYS_MON_FIRST


def set_start_day(pref: str) -> None:
    """Update both start-day keys so toggles stay consistent."""
    st.session_state.week_start = pref
    st.session_state.start_day_pref = pref


def next_schedule_color() -> str:
    """Assign the next palette color and advance the round-robin index."""
    idx = st.session_state.next_color_index % len(SCHEDULE_PALETTE)
    st.session_state.next_color_index = idx + 1
    return SCHEDULE_PALETTE[idx]


def time_to_minutes(hhmm: str) -> int:
    """Convert HH:MM to minutes since midnight."""
    hour, minute = map(int, hhmm.split(":"))
    return hour * 60 + minute


def format_clock(hhmm: str, time_format: str | None = None) -> str:
    """Format an HH:MM string using the active 12/24-hour preference."""
    tf = time_format or st.session_state.get("time_format", "12-Hour")
    hour, minute = map(int, hhmm.split(":"))
    if tf == "24-Hour":
        return f"{hour:02d}:{minute:02d}"
    suffix = "AM" if hour < 12 else "PM"
    hour12 = hour % 12 or 12
    return f"{hour12}:{minute:02d} {suffix}"


def format_hour_label(hour: int, time_format: str | None = None) -> str:
    """Format a whole-hour grid gutter label."""
    return format_clock(f"{hour:02d}:00", time_format)


def contrasting_text(hex_color: str) -> str:
    """Pick white or near-black text for readability on a hex background."""
    raw = hex_color.lstrip("#")
    if len(raw) != 6:
        return "#FFFFFF"
    r, g, b = int(raw[0:2], 16), int(raw[2:4], 16), int(raw[4:6], 16)
    luminance = (0.299 * r + 0.587 * g + 0.114 * b) / 255
    return "#FFFFFF" if luminance < 0.55 else "#1A1A1A"


def hex_to_rgba(hex_color: str, alpha: float = 0.92) -> str:
    """Convert #RRGGBB to an rgba() string."""
    raw = hex_color.lstrip("#")
    if len(raw) != 6:
        return f"rgba(16, 185, 129, {alpha})"
    r, g, b = int(raw[0:2], 16), int(raw[2:4], 16), int(raw[4:6], 16)
    return f"rgba({r}, {g}, {b}, {alpha})"


def update_schedule_color(schedule_id: str, color: str) -> None:
    """Propagate a layer color change to the schedule and all its events."""
    for sched in st.session_state.schedules:
        if sched["id"] == schedule_id:
            sched["color"] = color
    for event in st.session_state.events:
        if event["schedule_id"] == schedule_id:
            event["color"] = color


def resolve_layer_color(schedule_id: str, fallback: str = "#10B981") -> str:
    """Always resolve card color from the parent schedule layer (source of truth)."""
    for sched in st.session_state.schedules:
        if sched["id"] == schedule_id:
            return sched["color"]
    return fallback or "#10B981"


def refresh_schedule_event_counts() -> None:
    """Keep each layer's event_count in sync with session events."""
    counts: dict[str, int] = {}
    for event in st.session_state.events:
        sid = event.get("schedule_id", "")
        counts[sid] = counts.get(sid, 0) + 1
    for sched in st.session_state.schedules:
        sched["event_count"] = counts.get(sched["id"], 0)


def create_schedule_layer(name: str, color: str) -> str:
    """Append a named schedule layer with a user-chosen color."""
    schedule_id = str(uuid.uuid4())
    st.session_state.schedules.append(
        {
            "id": schedule_id,
            "name": name.strip() or "Untitled layer",
            "color": color,
            "filename": None,
            "fingerprint": None,
            "event_count": 0,
        }
    )
    return schedule_id


def delete_event(event_id: str) -> None:
    """Remove a single event by id and refresh the UI."""
    st.session_state.events = [
        e for e in st.session_state.events if e.get("id") != event_id
    ]
    refresh_schedule_event_counts()
    st.rerun()


def hhmm_from_time(value: dt_time) -> str:
    """Convert a datetime.time to HH:MM."""
    return f"{value.hour:02d}:{value.minute:02d}"


# ---------------------------------------------------------------------------
# Phase 3 — Local pdfplumber parser
# ---------------------------------------------------------------------------

def file_fingerprint(name: str, raw: bytes) -> str:
    """Stable id for an upload so the same PDF is not ingested twice."""
    digest = hashlib.sha256(raw).hexdigest()[:16]
    return f"{name}::{digest}"


def normalize_time(token: str) -> str | None:
    """Convert a loose time token into 24h HH:MM, or None if invalid."""
    token = token.strip()
    match = re.match(
        r"^(\d{1,2})(?::(\d{2}))?\s*([AaPp][Mm])?$",
        token,
    )
    if not match:
        return None

    hour = int(match.group(1))
    minute = int(match.group(2) or 0)
    meridiem = (match.group(3) or "").upper()

    if minute > 59:
        return None

    if meridiem:
        if hour < 1 or hour > 12:
            return None
        if meridiem == "AM":
            hour = 0 if hour == 12 else hour
        else:
            hour = hour if hour == 12 else hour + 12
    else:
        # Bare hour without meridian: treat 1–6 as PM-ish class times if ambiguous?
        # Prefer strict 24h when no AM/PM is present.
        if hour > 23:
            return None

    return f"{hour:02d}:{minute:02d}"


def expand_days_from_text(fragment: str) -> list[str]:
    """Extract canonical weekday names from a day-pattern fragment."""
    days: list[str] = []
    seen: set[str] = set()

    for pattern, mapped in COMPACT_DAY_PATTERNS:
        if pattern.search(fragment):
            for day in mapped:
                if day not in seen:
                    days.append(day)
                    seen.add(day)
            return days

    for match in DAY_LIST_RE.finditer(fragment):
        raw = match.group(0)
        parts = re.split(r"[/,&\s]+", raw)
        for part in parts:
            key = part.strip().lower()
            if not key:
                continue
            day = DAY_ALIASES.get(key)
            if day and day not in seen:
                days.append(day)
                seen.add(day)

    # Single full weekday somewhere in the fragment
    if not days:
        for key, day in DAY_ALIASES.items():
            if re.search(rf"\b{re.escape(key)}\b", fragment, re.I):
                if day not in seen:
                    days.append(day)
                    seen.add(day)

    return days


def _guess_title(line: str, day_span: tuple[int, int] | None, time_span: tuple[int, int]) -> str:
    """Pull a human title from the leftover text on a schedule line."""
    chars = list(line)
    for start, end in filter(None, [day_span, time_span]):
        for i in range(start, min(end, len(chars))):
            chars[i] = " "
    leftover = re.sub(r"\s+", " ", "".join(chars)).strip(" -–—|·•\t")
    leftover = re.sub(
        r"\b(room|rm|bldg|building|section|sec|loc(ation)?)\b[:\s].*$",
        "",
        leftover,
        flags=re.I,
    ).strip(" -–—|·•")
    if leftover:
        return leftover[:80]
    return "Untitled event"


def parse_schedule_line(line: str) -> list[dict[str, str]]:
    """
    Heuristically parse one text line into zero-or-more raw event dicts
    with keys: title, day, start, end.
    """
    line = line.strip()
    if not line or len(line) < 5:
        return []

    time_match = TIME_RANGE_RE.search(line)
    if not time_match:
        return []

    start = normalize_time(time_match.group(1))
    end = normalize_time(time_match.group(2))
    if not start or not end or start >= end:
        return []

    # Prefer day tokens outside the time span; search whole line
    before = line[: time_match.start()]
    after = line[time_match.end() :]
    day_region = f"{before} {after}"

    days = expand_days_from_text(day_region)
    if not days:
        # Sometimes days sit on the same side as the title with no separator
        days = expand_days_from_text(line)
    if not days:
        return []

    day_match = DAY_LIST_RE.search(day_region)
    day_span: tuple[int, int] | None = None
    if day_match:
        # Map day_region offsets back onto the original line when possible
        # Prefer searching the original line for the same match text.
        original = re.search(re.escape(day_match.group(0)), line, re.I)
        if original:
            day_span = original.span()
        else:
            for pattern, _ in COMPACT_DAY_PATTERNS:
                compact = pattern.search(line)
                if compact:
                    day_span = compact.span()
                    break

    if day_span is None:
        for pattern, _ in COMPACT_DAY_PATTERNS:
            compact = pattern.search(line)
            if compact:
                day_span = compact.span()
                break

    title = _guess_title(line, day_span, time_match.span())
    return [
        {"title": title, "day": day, "start": start, "end": end}
        for day in days
    ]


def extract_pdf_lines(pdf_bytes: bytes) -> list[str]:
    """Pull layout-aware text lines (and table cell rows) from a PDF."""
    lines: list[str] = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            text = page.extract_text(x_tolerance=2, y_tolerance=3) or ""
            for raw in text.splitlines():
                cleaned = re.sub(r"[ \t]+", " ", raw).strip()
                if cleaned:
                    lines.append(cleaned)

            # Table extraction — join cells on each row into a parseable line
            try:
                tables = page.extract_tables() or []
            except Exception:
                tables = []
            for table in tables:
                for row in table:
                    cells = [
                        re.sub(r"\s+", " ", (cell or "")).strip()
                        for cell in row
                        if cell and str(cell).strip()
                    ]
                    if cells:
                        lines.append(" | ".join(cells))
    return lines


def parse_schedule_pdf(
    pdf_bytes: bytes,
    *,
    schedule_id: str,
    color: str,
    source_name: str,
) -> tuple[list[dict[str, Any]], list[str]]:
    """
    Ingest PDF bytes and return (normalized events, warning messages).

    Events are expanded one-per-day for multi-day patterns (e.g. M/W → 2 events).
    """
    warnings: list[str] = []
    events: list[dict[str, Any]] = []

    try:
        lines = extract_pdf_lines(pdf_bytes)
    except Exception as exc:  # noqa: BLE001 — surface to the user as a warning
        warnings.append(f"{source_name}: could not open PDF ({exc}).")
        return [], warnings

    if not lines:
        warnings.append(
            f"{source_name}: no extractable text found. "
            "Verify the PDF is text-based (not a scan) or add events manually."
        )
        return [], warnings

    seen_keys: set[tuple[str, str, str, str]] = set()
    unmatched_with_time = 0

    for line in lines:
        parsed = parse_schedule_line(line)
        if not parsed and TIME_RANGE_RE.search(line):
            unmatched_with_time += 1
        for raw in parsed:
            key = (raw["title"].lower(), raw["day"], raw["start"], raw["end"])
            if key in seen_keys:
                continue
            seen_keys.add(key)
            events.append(
                _empty_event(
                    event_id=str(uuid.uuid4()),
                    title=raw["title"],
                    day=raw["day"],
                    start=raw["start"],
                    end=raw["end"],
                    schedule_id=schedule_id,
                    color=color,
                    source="parsed",
                )
            )

    if not events:
        warnings.append(
            f"{source_name}: parsing found no day+time blocks. "
            "Use the manual entry form (Phase 5) or verify the schedule layout."
        )
    elif unmatched_with_time:
        warnings.append(
            f"{source_name}: extracted {len(events)} event(s), but "
            f"{unmatched_with_time} time-range line(s) lacked a recognizable day. "
            "Please verify the overlay and add any missing blocks manually."
        )

    return events, warnings


def ingest_uploaded_files(
    uploads: list[Any] | None,
    target_schedule_id: str | None,
) -> None:
    """Parse uploaded PDFs into an existing schedule layer."""
    if not uploads or not target_schedule_id:
        return

    sched = next(
        (s for s in st.session_state.schedules if s["id"] == target_schedule_id),
        None,
    )
    if sched is None:
        return

    for upload in uploads:
        raw = upload.getvalue()
        fingerprint = file_fingerprint(upload.name, raw)
        # Allow same file into different layers; block exact re-ingest into same layer
        layer_fp = f"{fingerprint}::{target_schedule_id}"
        if layer_fp in st.session_state.processed_files:
            continue

        events, warnings = parse_schedule_pdf(
            raw,
            schedule_id=sched["id"],
            color=sched["color"],
            source_name=upload.name,
        )
        st.session_state.events.extend(events)
        st.session_state.processed_files.add(layer_fp)
        st.session_state.parse_warnings.extend(warnings)
        if events:
            sched["filename"] = upload.name

    refresh_schedule_event_counts()


def remove_schedule(schedule_id: str) -> None:
    """Drop a schedule layer and all of its events from the session."""
    st.session_state.schedules = [
        s for s in st.session_state.schedules if s["id"] != schedule_id
    ]
    st.session_state.events = [
        e for e in st.session_state.events if e["schedule_id"] != schedule_id
    ]
    # Drop color-picker widget state so a recreated layer does not inherit it
    color_key = f"color_{schedule_id}"
    if color_key in st.session_state:
        del st.session_state[color_key]
    # Clear processed fingerprints tied to this layer
    st.session_state.processed_files = {
        fp
        for fp in st.session_state.processed_files
        if not str(fp).endswith(f"::{schedule_id}")
    }


# ---------------------------------------------------------------------------
# Phase 4 — Weekly grid layout engine
# ---------------------------------------------------------------------------

def grid_hour_labels() -> list[int]:
    """Whole hours shown on the left gutter (7 .. 22)."""
    return list(range(GRID_START_HOUR, GRID_END_HOUR))


def layout_day_events(day_events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Assign absolute geometry + overlap lanes for one day's events.

    Adds: _top_px, _height_px, _left_pct, _width_pct, _conflict
    """
    if not day_events:
        return []

    grid_start = GRID_START_HOUR * 60
    grid_end = GRID_END_HOUR * 60
    grid_span = max(grid_end - grid_start, 1)

    prepared: list[dict[str, Any]] = []
    for event in day_events:
        start_m = max(time_to_minutes(event["start"]), grid_start)
        end_m = min(time_to_minutes(event["end"]), grid_end)
        if end_m <= start_m:
            continue
        item = dict(event)
        item["_top_px"] = ((start_m - grid_start) / 60) * HOUR_HEIGHT_PX
        item["_height_px"] = max(((end_m - start_m) / 60) * HOUR_HEIGHT_PX, 22)
        item["_start_m"] = start_m
        item["_end_m"] = end_m
        prepared.append(item)

    prepared.sort(key=lambda e: (e["_start_m"], e["_end_m"]))

    # Greedy lane assignment
    lane_ends: list[int] = []
    for item in prepared:
        placed = False
        for idx, lane_end in enumerate(lane_ends):
            if item["_start_m"] >= lane_end:
                lane_ends[idx] = item["_end_m"]
                item["_lane"] = idx
                placed = True
                break
        if not placed:
            item["_lane"] = len(lane_ends)
            lane_ends.append(item["_end_m"])

    # Cluster overlapping events so width uses the cluster's lane count
    n = len(prepared)
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i: int, j: int) -> None:
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[ri] = rj

    for i in range(n):
        for j in range(i + 1, n):
            if (
                prepared[i]["_start_m"] < prepared[j]["_end_m"]
                and prepared[j]["_start_m"] < prepared[i]["_end_m"]
            ):
                union(i, j)

    clusters: dict[int, list[int]] = {}
    for i in range(n):
        clusters.setdefault(find(i), []).append(i)

    for indices in clusters.values():
        # Compact lanes within the cluster
        members = [prepared[i] for i in indices]
        members.sort(key=lambda e: (e["_start_m"], e["_end_m"]))
        local_ends: list[int] = []
        for member in members:
            placed = False
            for idx, lane_end in enumerate(local_ends):
                if member["_start_m"] >= lane_end:
                    local_ends[idx] = member["_end_m"]
                    member["_lane"] = idx
                    placed = True
                    break
            if not placed:
                member["_lane"] = len(local_ends)
                local_ends.append(member["_end_m"])
        lane_count = max(len(local_ends), 1)
        conflict = lane_count > 1
        width = 100.0 / lane_count
        for member in members:
            member["_lane_count"] = lane_count
            member["_width_pct"] = width - 1.2
            member["_left_pct"] = member["_lane"] * width + 0.6
            member["_conflict"] = conflict

    # Silence unused grid_span (kept for clarity / future %)
    _ = grid_span
    return prepared


def render_weekly_grid() -> None:
    """Render the CSS weekly calendar with positioned, color-coded event cards."""
    days = ordered_days()
    hours = grid_hour_labels()
    total_height = len(hours) * HOUR_HEIGHT_PX
    tf = st.session_state.time_format

    events_by_day: dict[str, list[dict[str, Any]]] = {day: [] for day in days}
    for event in st.session_state.events:
        if event["day"] in events_by_day:
            events_by_day[event["day"]].append(event)

    # Header row
    header_cells = '<div class="so-grid-corner"></div>' + "".join(
        f'<div class="so-grid-day-header">{html.escape(day)}</div>' for day in days
    )

    # Time gutter labels
    gutter = "".join(
        f'<div class="so-grid-hour" style="height:{HOUR_HEIGHT_PX}px;">'
        f"{html.escape(format_hour_label(h, tf))}</div>"
        for h in hours
    )

    # Day columns with hour lines + event cards
    day_columns: list[str] = []
    for day in days:
        laid_out = layout_day_events(events_by_day[day])
        lines = "".join(
            f'<div class="so-grid-hline" style="top:{i * HOUR_HEIGHT_PX}px;"></div>'
            for i in range(len(hours) + 1)
        )
        cards: list[str] = []
        for ev in laid_out:
            title = html.escape(ev["title"])
            time_label = html.escape(
                f"{format_clock(ev['start'], tf)} – {format_clock(ev['end'], tf)}"
            )
            # Layer color is authoritative — never trust a stale event.color copy
            layer_color = resolve_layer_color(ev.get("schedule_id", ""), ev.get("color", "#10B981"))
            bg = hex_to_rgba(layer_color, 0.90)
            fg = contrasting_text(layer_color)
            conflict_cls = " so-conflict" if ev.get("_conflict") else ""
            conflict_icon = (
                '<span class="so-conflict-badge" title="Time conflict">⚠</span>'
                if ev.get("_conflict")
                else ""
            )
            cards.append(
                f'<div class="so-event-card{conflict_cls}" '
                f'style="top:{ev["_top_px"]:.1f}px;height:{ev["_height_px"]:.1f}px;'
                f'left:{ev["_left_pct"]:.2f}%;width:{ev["_width_pct"]:.2f}%;'
                f'background:{bg};color:{fg};border-left:4px solid {layer_color};">'
                f'{conflict_icon}'
                f'<div class="so-event-title">{title}</div>'
                f'<div class="so-event-time">{time_label}</div>'
                f"</div>"
            )
        day_columns.append(
            f'<div class="so-grid-day" style="height:{total_height}px;">'
            f"{lines}{''.join(cards)}</div>"
        )

    body = (
        f'<div class="so-grid-gutter" style="height:{total_height}px;">{gutter}</div>'
        + "".join(day_columns)
    )

    markup = f"""
    <div class="so-week-grid">
      <div class="so-grid-header">{header_cells}</div>
      <div class="so-grid-body">{body}</div>
    </div>
    """
    st.markdown(markup, unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Phase 5 — Saved schedules (max 10) + PDF export
# ---------------------------------------------------------------------------

def _clear_layer_color_keys() -> None:
    """Drop color-picker widget keys so loaded layers get fresh pickers."""
    stale = [
        key
        for key in list(st.session_state.keys())
        if isinstance(key, str) and key.startswith("color_")
    ]
    for key in stale:
        del st.session_state[key]


def build_saved_snapshot(name: str) -> dict[str, Any]:
    """Deep-copy the active calendar into a saved-schedule object."""
    return {
        "id": str(uuid.uuid4()),
        "name": name.strip(),
        "events": deepcopy(list(st.session_state.events)),
        "schedules": deepcopy(list(st.session_state.schedules)),
        "calendar_title": st.session_state.calendar_title,
        "season": st.session_state.season,
        "week_start": st.session_state.week_start,
        "time_format": st.session_state.time_format,
    }


def save_current_schedule(name: str) -> tuple[bool, str]:
    """
    Persist the active calendar into saved_schedules.

    Returns (ok, message). Enforces MAX_SAVED_SCHEDULES.
    """
    clean = (name or "").strip()
    if not clean:
        return False, "Enter a name for this saved schedule."
    if not st.session_state.schedules and not st.session_state.events:
        return False, "Nothing to save — add a layer or event first."
    if len(st.session_state.saved_schedules) >= MAX_SAVED_SCHEDULES:
        return (
            False,
            f"Limit reached ({MAX_SAVED_SCHEDULES}). Delete a saved schedule first.",
        )
    if any(s["name"].lower() == clean.lower() for s in st.session_state.saved_schedules):
        return False, "A saved schedule with that name already exists."

    st.session_state.saved_schedules.append(build_saved_snapshot(clean))
    return True, f'Saved “{clean}” ({len(st.session_state.saved_schedules)}/{MAX_SAVED_SCHEDULES}).'


def load_saved_schedule(saved_id: str) -> tuple[bool, str]:
    """Replace the active session calendar with a deep copy of a saved snapshot."""
    snapshot = next(
        (s for s in st.session_state.saved_schedules if s["id"] == saved_id),
        None,
    )
    if snapshot is None:
        return False, "Saved schedule not found."

    _clear_layer_color_keys()
    st.session_state.events = deepcopy(snapshot.get("events", []))
    st.session_state.schedules = deepcopy(snapshot.get("schedules", []))
    st.session_state.calendar_title = snapshot.get("calendar_title", "Schedule")
    st.session_state.season = snapshot.get("season", "Fall")
    week = snapshot.get("week_start", "Monday")
    set_start_day(week if week in ("Monday", "Sunday") else "Monday")
    st.session_state.time_format = snapshot.get("time_format", "12-Hour")
    st.session_state.processed_files = set()
    st.session_state.parse_warnings = []
    st.session_state.edit_event_id = None
    refresh_schedule_event_counts()
    return True, f'Loaded “{snapshot["name"]}”.'


def delete_saved_schedule(saved_id: str) -> None:
    """Remove one entry from the saved-schedules list."""
    st.session_state.saved_schedules = [
        s for s in st.session_state.saved_schedules if s["id"] != saved_id
    ]


def _pdf_hex(color: str, fallback: str = "#10B981") -> HexColor:
    try:
        return HexColor(color)
    except Exception:
        return HexColor(fallback)


def build_calendar_pdf_bytes() -> bytes:
    """
    Render the active weekly grid into a landscape PDF (reportlab, offline).

    Matches calendar title, season palette, week-start order, and time format.
    """
    buffer = io.BytesIO()
    page_w, page_h = landscape(letter)
    c = pdf_canvas.Canvas(buffer, pagesize=landscape(letter))

    theme = get_season_theme(st.session_state.season)
    header_bg = _pdf_hex(theme["--header-bg"], "#1E3A5F")
    accent = _pdf_hex(theme["--accent"], "#38BDF8")
    surface = _pdf_hex(theme["--bg-surface"], "#FFFFFF")
    grid_line = _pdf_hex(theme["--grid-line"], "#CBD5E1")
    text_primary = _pdf_hex(theme["--text-primary"], "#0F172A")
    text_secondary = _pdf_hex(theme["--text-secondary"], "#475569")
    conflict_color = _pdf_hex(theme["--conflict"], "#DC2626")

    margin = 28
    # Title banner
    banner_h = 42
    c.setFillColor(header_bg)
    c.roundRect(margin, page_h - margin - banner_h, page_w - 2 * margin, banner_h, 6, fill=1, stroke=0)
    c.setStrokeColor(accent)
    c.setLineWidth(3)
    c.line(margin, page_h - margin - banner_h, page_w - margin, page_h - margin - banner_h)

    c.setFillColor(white)
    c.setFont("Helvetica-Bold", 16)
    title = st.session_state.calendar_title or "Schedule"
    c.drawString(margin + 12, page_h - margin - 18, title[:70])
    c.setFont("Helvetica", 9)
    subtitle = (
        f"{st.session_state.season} · week starts {st.session_state.start_day_pref} · "
        f"{st.session_state.time_format}"
    )
    c.drawString(margin + 12, page_h - margin - 34, subtitle)

    # Layer legend
    legend_y = page_h - margin - banner_h - 18
    c.setFont("Helvetica", 8)
    lx = margin
    for sched in st.session_state.schedules:
        c.setFillColor(_pdf_hex(sched["color"]))
        c.circle(lx + 4, legend_y, 4, fill=1, stroke=0)
        c.setFillColor(text_primary)
        label = f'{sched["name"]} ({sched.get("event_count", 0)})'
        c.drawString(lx + 12, legend_y - 3, label[:28])
        lx += 12 + c.stringWidth(label[:28], "Helvetica", 8) + 14
        if lx > page_w - margin - 80:
            lx = margin
            legend_y -= 12

    # Grid geometry
    days = ordered_days()
    hours = grid_hour_labels()
    grid_top = legend_y - 14
    grid_bottom = margin + 20
    grid_left = margin + 48
    grid_right = page_w - margin
    grid_h = grid_top - grid_bottom
    grid_w = grid_right - grid_left
    col_w = grid_w / 7
    row_h = grid_h / len(hours)
    tf = st.session_state.time_format

    # Background + day headers
    c.setFillColor(surface)
    c.rect(grid_left, grid_bottom, grid_w, grid_h, fill=1, stroke=0)
    header_h = 18
    c.setFillColor(header_bg)
    c.rect(grid_left, grid_top - header_h, grid_w, header_h, fill=1, stroke=0)
    c.setFillColor(white)
    c.setFont("Helvetica-Bold", 8)
    for i, day in enumerate(days):
        c.drawCentredString(grid_left + (i + 0.5) * col_w, grid_top - 12, day[:3])

    plot_top = grid_top - header_h
    plot_h = plot_top - grid_bottom

    # Hour lines + labels
    c.setStrokeColor(grid_line)
    c.setLineWidth(0.5)
    c.setFillColor(text_secondary)
    c.setFont("Helvetica", 6.5)
    for i, hour in enumerate(hours):
        y = plot_top - (i / len(hours)) * plot_h
        c.line(grid_left, y, grid_right, y)
        c.drawRightString(grid_left - 4, y - 3, format_hour_label(hour, tf))
    c.line(grid_left, grid_bottom, grid_right, grid_bottom)

    # Day columns
    for i in range(8):
        x = grid_left + i * col_w
        c.line(x, grid_bottom, x, grid_top)

    # Events
    events_by_day: dict[str, list[dict[str, Any]]] = {d: [] for d in days}
    for event in st.session_state.events:
        if event["day"] in events_by_day:
            events_by_day[event["day"]].append(event)

    grid_start_m = GRID_START_HOUR * 60
    grid_end_m = GRID_END_HOUR * 60
    span_m = max(grid_end_m - grid_start_m, 1)

    for day_idx, day in enumerate(days):
        laid = layout_day_events(events_by_day[day])
        for ev in laid:
            start_m = max(time_to_minutes(ev["start"]), grid_start_m)
            end_m = min(time_to_minutes(ev["end"]), grid_end_m)
            if end_m <= start_m:
                continue
            top_frac = (start_m - grid_start_m) / span_m
            height_frac = (end_m - start_m) / span_m
            ev_top = plot_top - top_frac * plot_h
            ev_h = max(height_frac * plot_h, 8)
            left_pct = ev.get("_left_pct", 0.6) / 100.0
            width_pct = ev.get("_width_pct", 98.0) / 100.0
            x = grid_left + day_idx * col_w + left_pct * col_w
            w = max(width_pct * col_w, 8)
            y = ev_top - ev_h

            color = resolve_layer_color(ev.get("schedule_id", ""), ev.get("color", "#10B981"))
            fill = _pdf_hex(color)
            c.setFillColor(fill)
            c.setStrokeColor(conflict_color if ev.get("_conflict") else fill)
            c.setLineWidth(1.5 if ev.get("_conflict") else 0.6)
            c.roundRect(x, y, w, ev_h, 2, fill=1, stroke=1)

            # Readable text
            luminance = (
                0.299 * fill.red + 0.587 * fill.green + 0.114 * fill.blue
            )
            c.setFillColor(white if luminance < 0.55 else black)
            c.setFont("Helvetica-Bold", 6)
            label = (ev.get("title") or "Event")[:22]
            if ev_h >= 10:
                c.drawString(x + 2, y + ev_h - 7, label)
            if ev_h >= 18:
                c.setFont("Helvetica", 5.5)
                time_lbl = f"{format_clock(ev['start'], tf)}-{format_clock(ev['end'], tf)}"
                c.drawString(x + 2, y + ev_h - 14, time_lbl[:20])

    c.setFillColor(text_secondary)
    c.setFont("Helvetica", 7)
    c.drawString(margin, 10, "Schedule Overlays — local export")
    c.showPage()
    c.save()
    return buffer.getvalue()


def render_saved_schedules_panel() -> None:
    """Sidebar: save / load / delete up to MAX_SAVED_SCHEDULES calendars."""
    st.markdown(f"**Saved schedules** ({len(st.session_state.saved_schedules)}/{MAX_SAVED_SCHEDULES})")

    with st.form("save_schedule_form", clear_on_submit=True):
        save_name = st.text_input(
            "Save current as",
            placeholder="e.g. Fall 2026 QC + BTT",
        )
        do_save = st.form_submit_button("Save schedule", use_container_width=True)
        if do_save:
            ok, msg = save_current_schedule(save_name)
            if ok:
                st.success(msg)
                st.rerun()
            else:
                st.warning(msg)

    if not st.session_state.saved_schedules:
        st.caption("No saved schedules yet.")
        return

    for snap in list(st.session_state.saved_schedules):
        c1, c2, c3 = st.columns([0.5, 0.25, 0.25])
        with c1:
            st.caption(
                f'**{snap["name"]}** · {len(snap.get("events", []))} evt · '
                f'{snap.get("season", "—")}'
            )
        with c2:
            if st.button("Load", key=f"load_saved_{snap['id']}", use_container_width=True):
                ok, msg = load_saved_schedule(snap["id"])
                if ok:
                    st.success(msg)
                    st.rerun()
                else:
                    st.warning(msg)
        with c3:
            if st.button("Delete", key=f"del_saved_{snap['id']}", use_container_width=True):
                delete_saved_schedule(snap["id"])
                st.rerun()


def render_pdf_export_panel() -> None:
    """Main-pane download button for the compiled calendar PDF."""
    st.markdown("### Export")
    try:
        pdf_bytes = build_calendar_pdf_bytes()
    except Exception as exc:  # noqa: BLE001
        st.error(f"PDF export failed: {exc}")
        return

    safe_name = re.sub(r"[^\w\-]+", "_", st.session_state.calendar_title).strip("_") or "schedule"
    st.download_button(
        label="Download calendar PDF",
        data=pdf_bytes,
        file_name=f"{safe_name}.pdf",
        mime="application/pdf",
        use_container_width=False,
        help="Exports the current weekly grid with your title, theme, and layers.",
    )


# ---------------------------------------------------------------------------
# Phase 2 — Four Seasons theme injection
# ---------------------------------------------------------------------------

def get_season_theme(season: str) -> dict[str, str]:
    """Map a season name to its CSS custom-property dictionary."""
    return SEASON_THEMES.get(season, SEASON_THEMES["Fall"])


def apply_theme(season: str | None = None) -> None:
    """Inject global CSS variables and Streamlit chrome overrides for the season."""
    season = season or st.session_state.get("season", "Fall")
    theme = get_season_theme(season)
    var_block = "\n".join(f"  {k}: {v};" for k, v in theme.items())

    css = f"""
    <style>
    :root {{
{var_block}
    }}

    /* Kill Streamlit's default dark header strip above the banner */
    header[data-testid="stHeader"],
    [data-testid="stHeader"] {{
      background: transparent !important;
      background-color: transparent !important;
      background-image: none !important;
      box-shadow: none !important;
      border: none !important;
      height: 0 !important;
      min-height: 0 !important;
      overflow: hidden !important;
    }}
    [data-testid="stDecoration"],
    [data-testid="stToolbar"],
    [data-testid="stStatusWidget"] {{
      display: none !important;
      background: transparent !important;
    }}
    .stApp > header {{
      background: transparent !important;
    }}
    .block-container {{
      padding-top: 1.25rem !important;
    }}
    /* Prevent empty markdown/style wrappers from painting a bar */
    .stMarkdown:has(> style),
    div[data-testid="stMarkdownContainer"]:has(> style) {{
      display: none !important;
      margin: 0 !important;
      padding: 0 !important;
      height: 0 !important;
    }}

    .stApp {{
      background: linear-gradient(
        165deg,
        var(--bg-primary) 0%,
        var(--bg-secondary) 55%,
        var(--bg-primary) 100%
      ) !important;
      color: var(--text-primary);
    }}

    [data-testid="stSidebar"] {{
      background: linear-gradient(
        180deg,
        var(--bg-sidebar) 0%,
        color-mix(in srgb, var(--bg-sidebar) 85%, #000) 100%
      ) !important;
    }}
    [data-testid="stSidebar"] * {{
      color: var(--text-inverse) !important;
    }}
    [data-testid="stSidebar"] .stCaption,
    [data-testid="stSidebar"] label {{
      color: color-mix(in srgb, var(--text-inverse) 85%, transparent) !important;
    }}
    [data-testid="stSidebar"] [data-baseweb="select"] > div,
    [data-testid="stSidebar"] input,
    [data-testid="stSidebar"] textarea {{
      background-color: color-mix(in srgb, var(--bg-surface) 18%, transparent) !important;
      border-color: color-mix(in srgb, var(--text-inverse) 35%, transparent) !important;
    }}

    /* Accent-driven interactive chrome (Spring pink when season=Spring) */
    .stButton > button {{
      border: 1px solid var(--accent) !important;
      transition: background 0.15s ease, border-color 0.15s ease, box-shadow 0.15s ease;
    }}
    .stButton > button:hover {{
      border-color: var(--accent) !important;
      box-shadow: 0 0 0 3px color-mix(in srgb, var(--accent) 35%, transparent) !important;
    }}
    [data-testid="stSidebar"] .stButton > button {{
      background: color-mix(in srgb, var(--accent) 40%, transparent) !important;
      border-color: var(--accent) !important;
    }}
    [data-testid="stSidebar"] .stButton > button:hover {{
      background: color-mix(in srgb, var(--accent) 60%, transparent) !important;
    }}
    div[role="radiogroup"] label[data-baseweb="radio"] > div:first-child {{
      border-color: color-mix(in srgb, var(--accent) 70%, var(--border)) !important;
    }}
    div[role="radiogroup"] label[data-baseweb="radio"] input:checked + div {{
      background-color: var(--accent) !important;
      border-color: var(--accent) !important;
    }}
    [data-baseweb="slider"] div[role="slider"] {{
      background-color: var(--accent) !important;
    }}
    .stSlider [data-testid="stThumbValue"],
    .stMultiSelect [data-baseweb="tag"] {{
      background-color: var(--accent) !important;
    }}

    .so-title-banner {{
      background: var(--header-bg);
      color: var(--header-text);
      padding: 1rem 1.25rem;
      border-radius: 10px;
      margin: 0 0 0.75rem 0;
      box-shadow: 0 4px 18px var(--shadow);
      border: 1px solid color-mix(in srgb, var(--accent) 40%, transparent);
      border-bottom: 4px solid var(--accent);
    }}
    .so-title-banner h1 {{
      margin: 0;
      font-size: 1.75rem;
      font-weight: 700;
      letter-spacing: 0.02em;
      color: var(--header-text) !important;
    }}
    .so-title-banner .so-subtitle {{
      margin-top: 0.35rem;
      opacity: 0.85;
      font-size: 0.95rem;
    }}

    .so-theme-preview {{
      display: flex;
      gap: 0.5rem;
      flex-wrap: wrap;
      margin: 0.5rem 0 1rem;
    }}
    .so-swatch {{
      width: 2.5rem;
      height: 2.5rem;
      border-radius: 8px;
      border: 2px solid var(--border);
      box-shadow: 0 2px 8px var(--shadow);
    }}

    .so-card {{
      background: var(--bg-surface);
      border: 1px solid var(--grid-line);
      border-radius: 10px;
      padding: 1rem;
      box-shadow: 0 2px 12px var(--shadow);
      color: var(--text-primary);
    }}
    .so-accent-bar {{
      height: 4px;
      border-radius: 2px;
      background: linear-gradient(90deg, var(--accent), var(--accent-soft));
      margin-bottom: 0.75rem;
    }}

    .so-conflict {{
      outline: 2px solid var(--conflict);
      box-shadow: 0 0 0 3px color-mix(in srgb, var(--conflict) 25%, transparent);
    }}

    .so-schedule-chip {{
      display: inline-flex;
      align-items: center;
      gap: 0.4rem;
      padding: 0.25rem 0.65rem;
      border-radius: 999px;
      background: var(--bg-surface);
      border: 1px solid var(--grid-line);
      margin: 0.15rem 0.25rem 0.15rem 0;
      font-size: 0.85rem;
    }}
    .so-schedule-chip.so-active-layer {{
      border-color: var(--accent);
      box-shadow: 0 0 0 3px color-mix(in srgb, var(--accent) 30%, transparent);
      background: color-mix(in srgb, var(--accent-soft) 55%, var(--bg-surface));
    }}
    .so-schedule-dot {{
      width: 0.7rem;
      height: 0.7rem;
      border-radius: 50%;
      flex-shrink: 0;
    }}

    /* Weekly calendar grid */
    .so-week-grid {{
      background: var(--bg-surface);
      border: 1px solid var(--grid-line);
      border-radius: 12px;
      overflow: hidden;
      box-shadow: 0 4px 18px var(--shadow);
      margin: 0.75rem 0 1.25rem;
    }}
    .so-grid-header {{
      display: grid;
      grid-template-columns: 72px repeat(7, minmax(0, 1fr));
      background: var(--header-bg);
      color: var(--header-text);
    }}
    .so-grid-corner {{
      border-right: 1px solid color-mix(in srgb, var(--header-text) 20%, transparent);
    }}
    .so-grid-day-header {{
      text-align: center;
      font-weight: 650;
      font-size: 0.85rem;
      padding: 0.65rem 0.25rem;
      border-right: 1px solid color-mix(in srgb, var(--header-text) 18%, transparent);
      letter-spacing: 0.02em;
    }}
    .so-grid-day-header:last-child {{
      border-right: none;
    }}
    .so-grid-body {{
      display: grid;
      grid-template-columns: 72px repeat(7, minmax(0, 1fr));
      background: var(--bg-surface);
    }}
    .so-grid-gutter {{
      border-right: 1px solid var(--grid-line);
      background: color-mix(in srgb, var(--bg-secondary) 55%, var(--bg-surface));
    }}
    .so-grid-hour {{
      display: flex;
      align-items: flex-start;
      justify-content: flex-end;
      padding: 2px 8px 0 4px;
      font-size: 0.72rem;
      color: var(--text-secondary);
      border-bottom: 1px solid var(--grid-line);
      box-sizing: border-box;
    }}
    .so-grid-day {{
      position: relative;
      border-right: 1px solid var(--grid-line);
      background:
        repeating-linear-gradient(
          to bottom,
          transparent,
          transparent calc({HOUR_HEIGHT_PX}px - 1px),
          var(--grid-line) calc({HOUR_HEIGHT_PX}px - 1px),
          var(--grid-line) {HOUR_HEIGHT_PX}px
        );
    }}
    .so-grid-day:last-child {{
      border-right: none;
    }}
    .so-grid-hline {{
      position: absolute;
      left: 0;
      right: 0;
      height: 0;
      border-top: 1px solid var(--grid-line);
      pointer-events: none;
    }}
    .so-event-card {{
      position: absolute;
      z-index: 2;
      box-sizing: border-box;
      border-radius: 6px;
      padding: 4px 6px;
      overflow: hidden;
      font-size: 0.72rem;
      line-height: 1.25;
      box-shadow: 0 1px 4px var(--shadow);
      border: 1px solid color-mix(in srgb, #000 12%, transparent);
    }}
    .so-event-title {{
      font-weight: 700;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }}
    .so-event-time {{
      opacity: 0.92;
      font-size: 0.68rem;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }}
    .so-conflict-badge {{
      position: absolute;
      top: 2px;
      right: 4px;
      font-size: 0.7rem;
      line-height: 1;
    }}

    [data-testid="stMetric"] {{
      background: var(--bg-surface);
      border: 1px solid var(--grid-line);
      border-radius: 10px;
      padding: 0.75rem 1rem;
      box-shadow: 0 2px 10px var(--shadow);
    }}
    div[data-testid="stAlert"] {{
      border-radius: 10px;
      border-left: 4px solid var(--accent) !important;
    }}
    </style>
    """
    st.markdown(css, unsafe_allow_html=True)


def render_theme_preview() -> None:
    """Small swatch row so theme changes are obvious before the grid exists."""
    theme = get_season_theme(st.session_state.season)
    keys = [
        "--bg-primary",
        "--bg-secondary",
        "--bg-sidebar",
        "--accent",
        "--accent-soft",
        "--header-bg",
    ]
    swatches = "".join(
        f'<div class="so-swatch" style="background:{theme[k]};" title="{k}"></div>'
        for k in keys
    )
    st.markdown(
        f'<div class="so-theme-preview">{swatches}</div>',
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

def render_layer_panel() -> None:
    """Create / recolor / remove schedule layers."""
    st.markdown("**Schedule layers**")

    with st.expander("Add new layer", expanded=not st.session_state.schedules):
        with st.form("create_layer_form", clear_on_submit=True):
            layer_name = st.text_input(
                "Layer name",
                placeholder="e.g. QC Classes, BTT Classes",
            )
            layer_color = st.color_picker(
                "Layer color",
                value=SCHEDULE_PALETTE[
                    st.session_state.next_color_index % len(SCHEDULE_PALETTE)
                ],
                help="Custom hex color for every event on this layer.",
            )
            submitted = st.form_submit_button("Create layer", use_container_width=True)
            if submitted:
                name = (layer_name or "").strip()
                if not name:
                    st.warning("Enter a layer name first.")
                elif any(s["name"].lower() == name.lower() for s in st.session_state.schedules):
                    st.warning("A layer with that name already exists.")
                else:
                    create_schedule_layer(name, layer_color)
                    st.session_state.next_color_index = (
                        st.session_state.next_color_index + 1
                    ) % len(SCHEDULE_PALETTE)
                    st.rerun()

    if not st.session_state.schedules:
        st.caption("Create a layer before uploading PDFs or adding events.")
        return

    for sched in list(st.session_state.schedules):
        c1, c2 = st.columns([0.72, 0.28])
        with c1:
            new_color = st.color_picker(
                f'{sched["name"]} ({sched["event_count"]})',
                value=sched["color"],
                key=f"color_{sched['id']}",
                help="Live-updates every event card on this layer.",
            )
            if new_color != sched["color"]:
                update_schedule_color(sched["id"], new_color)
        with c2:
            if st.button("Remove", key=f"rm_layer_{sched['id']}", use_container_width=True):
                remove_schedule(sched["id"])
                st.rerun()


def render_upload_panel() -> None:
    """Upload PDFs mapped onto a chosen existing layer."""
    st.markdown("**Import PDF into layer**")
    if not st.session_state.schedules:
        st.caption("Create a schedule layer first, then upload.")
        return

    layer_labels = {s["id"]: s["name"] for s in st.session_state.schedules}
    target_id = st.selectbox(
        "Assign upload to layer",
        options=list(layer_labels.keys()),
        format_func=lambda sid: layer_labels[sid],
        key="upload_target_layer",
        help="Parsed events are added to this layer and inherit its color.",
    )
    uploads = st.file_uploader(
        "Upload schedule PDFs",
        type=["pdf"],
        accept_multiple_files=True,
        help="Parsed entirely locally with pdfplumber — no external APIs.",
        key="pdf_uploader",
    )
    ingest_uploaded_files(uploads, target_id)


def render_manual_event_form() -> None:
    """Manually append an event onto a chosen layer."""
    st.markdown("**Add event manually**")
    if not st.session_state.schedules:
        st.caption("Create a schedule layer first.")
        return

    layer_labels = {s["id"]: s["name"] for s in st.session_state.schedules}
    with st.form("manual_event_form", clear_on_submit=True):
        title = st.text_input("Title / course name")
        days = st.multiselect(
            "Day(s)",
            options=DAYS_MON_FIRST,
            default=["Monday"],
        )
        c1, c2 = st.columns(2)
        with c1:
            start_t = st.time_input("Start time", value=dt_time(9, 0), step=300)
        with c2:
            end_t = st.time_input("End time", value=dt_time(10, 0), step=300)
        layer_id = st.selectbox(
            "Schedule layer",
            options=list(layer_labels.keys()),
            format_func=lambda sid: layer_labels[sid],
        )
        st.caption(
            f"Times preview: {format_clock(hhmm_from_time(start_t))} – "
            f"{format_clock(hhmm_from_time(end_t))}"
        )
        saved = st.form_submit_button("Add event", use_container_width=True)
        if saved:
            start = hhmm_from_time(start_t)
            end = hhmm_from_time(end_t)
            if not (title or "").strip():
                st.warning("Title is required.")
            elif not days:
                st.warning("Pick at least one day.")
            elif start >= end:
                st.warning("End time must be after start time.")
            else:
                color = resolve_layer_color(layer_id)
                for day in days:
                    st.session_state.events.append(
                        _empty_event(
                            event_id=str(uuid.uuid4()),
                            title=title.strip(),
                            day=day,
                            start=start,
                            end=end,
                            schedule_id=layer_id,
                            color=color,
                            source="manual",
                        )
                    )
                refresh_schedule_event_counts()
                st.rerun()


def render_sidebar_foundation() -> None:
    """Sidebar: title, week start, time format, theme, layers, upload, manual add."""
    with st.sidebar:
        st.header("Schedule Overlays")
        st.caption("Local PDF overlay calendar")

        st.session_state.calendar_title = st.text_input(
            "Calendar title",
            value=st.session_state.calendar_title,
            help="Shown as the banner above the weekly grid and on exported PDFs.",
        )

        start_choice = st.radio(
            "Start week on",
            options=["Monday", "Sunday"],
            index=0 if st.session_state.start_day_pref == "Monday" else 1,
            horizontal=True,
            help="Reorders the day columns in the weekly grid.",
        )
        set_start_day(start_choice)

        st.radio(
            "Time format",
            options=["12-Hour", "24-Hour"],
            horizontal=True,
            key="time_format",
            help="Updates hour labels, event cards, and time pickers.",
        )

        st.divider()
        st.selectbox(
            "Season theme",
            options=SEASONS,
            key="season",
            help="Winter / Spring / Summer / Fall — updates colors layout-wide.",
        )

        st.divider()
        render_saved_schedules_panel()
        st.divider()
        render_layer_panel()
        st.divider()
        render_upload_panel()
        st.divider()
        render_manual_event_form()


def render_event_manager() -> None:
    """List events with working per-event Remove buttons."""
    if not st.session_state.events:
        return

    with st.expander("Manage events", expanded=True):
        for event in list(st.session_state.events):
            layer_name = next(
                (
                    s["name"]
                    for s in st.session_state.schedules
                    if s["id"] == event["schedule_id"]
                ),
                "—",
            )
            layer_color = resolve_layer_color(
                event.get("schedule_id", ""),
                event.get("color", "#10B981"),
            )
            c1, c2, c3, c4 = st.columns([3.2, 2.2, 2.2, 1.2])
            with c1:
                st.markdown(
                    f'<span class="so-schedule-dot" style="background:{html.escape(layer_color)};'
                    f'display:inline-block;margin-right:0.35rem;vertical-align:middle;"></span>'
                    f'**{html.escape(event["title"])}**',
                    unsafe_allow_html=True,
                )
            with c2:
                st.caption(
                    f'{event["day"]} · {format_clock(event["start"])}–{format_clock(event["end"])}'
                )
            with c3:
                st.caption(layer_name)
            with c4:
                if st.button(
                    "Remove",
                    key=f"del_ev_{event['id']}",
                    use_container_width=True,
                ):
                    delete_event(event["id"])


def render_parse_warnings() -> None:
    """Inline fallback messaging when automated parsing is incomplete."""
    if not st.session_state.parse_warnings:
        return
    for msg in st.session_state.parse_warnings:
        st.warning(msg)
    st.info(
        "If any blocks are missing, add them with the sidebar form or remove "
        "individual events below and re-import a clearer PDF."
    )
    if st.button("Dismiss parse warnings"):
        st.session_state.parse_warnings = []
        st.rerun()


def render_main_foundation() -> None:
    """Main pane: themed banner + weekly grid + event manager."""
    season = st.session_state.season
    st.markdown(
        f"""
        <div class="so-title-banner">
          <h1>{html.escape(st.session_state.calendar_title)}</h1>
          <div class="so-subtitle">{html.escape(season)} theme · week starts {html.escape(st.session_state.start_day_pref)} · {html.escape(st.session_state.time_format)}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    render_theme_preview()

    active_upload_layer = st.session_state.get("upload_target_layer")
    chips = "".join(
        f'<span class="so-schedule-chip'
        f'{" so-active-layer" if s["id"] == active_upload_layer else ""}">'
        f'<span class="so-schedule-dot" style="background:{html.escape(s["color"])};"></span>'
        f'{html.escape(s["name"])}</span>'
        for s in st.session_state.schedules
    )
    if chips:
        st.markdown(chips, unsafe_allow_html=True)

    col_a, col_b, col_c = st.columns(3)
    with col_a:
        st.metric("Schedules", len(st.session_state.schedules))
    with col_b:
        st.metric("Events", len(st.session_state.events))
    with col_c:
        st.metric("Saved", f"{len(st.session_state.saved_schedules)}/{MAX_SAVED_SCHEDULES}")

    render_parse_warnings()
    render_weekly_grid()
    render_pdf_export_panel()

    if not st.session_state.schedules:
        st.info(
            "Create a schedule layer in the sidebar (with your custom color), "
            "then upload a PDF into that layer or add events manually."
        )
    elif not st.session_state.events:
        st.info(
            "Upload a PDF into a layer or use **Add event manually** in the sidebar."
        )

    render_event_manager()

    with st.expander("Session state schema (debug)"):
        st.json(
            {
                "calendar_title": st.session_state.calendar_title,
                "start_day_pref": st.session_state.start_day_pref,
                "week_start": st.session_state.week_start,
                "time_format": st.session_state.time_format,
                "season": st.session_state.season,
                "schedules": st.session_state.schedules,
                "events": st.session_state.events,
                "saved_schedules": [
                    {
                        "id": s["id"],
                        "name": s["name"],
                        "event_count": len(s.get("events", [])),
                        "layer_count": len(s.get("schedules", [])),
                        "season": s.get("season"),
                        "week_start": s.get("week_start"),
                    }
                    for s in st.session_state.saved_schedules
                ],
                "parse_warnings": st.session_state.parse_warnings,
                "ordered_days": ordered_days(),
            }
        )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    st.set_page_config(
        page_title="Schedule Overlays",
        page_icon="📅",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    init_session_state()
    apply_theme()
    render_sidebar_foundation()
    render_main_foundation()


if __name__ == "__main__":
    main()
