"""
Gemini AI Solver — generates a monthly schedule using Google Gemini.

Loads the same Supabase data as the OR-Tools solver, serialises the problem
as a structured JSON prompt, calls Gemini 2.0 Flash, parses the response,
validates it, and writes the entries back with source='solver'.
"""
from __future__ import annotations

import json
import os
import re
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Tuple

import google.generativeai as genai
from dateutil.relativedelta import relativedelta

# ── Gemini configuration ──────────────────────────────────────
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

def _configure_gemini():
    if not GEMINI_API_KEY:
        raise RuntimeError(
            "GEMINI_API_KEY is not set. Set the env var to your Google AI Studio key."
        )
    genai.configure(api_key=GEMINI_API_KEY)


# ── Vaud public holidays (same logic as main solver) ──────────
def _get_vaud_holidays(year: int) -> set:
    """Return a set of date objects for Canton de Vaud public holidays."""
    holidays = {
        date(year, 1, 1),   # Nouvel An
        date(year, 1, 2),   # Berchtoldstag
        date(year, 5, 1),   # Fête du travail
        date(year, 8, 1),   # Fête nationale
        date(year, 12, 25), # Noël
    }
    # Easter-based (Meeus algorithm)
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    easter = date(year, month, day)
    holidays.add(easter + timedelta(days=-2))   # Vendredi Saint
    holidays.add(easter + timedelta(days=1))    # Lundi de Pâques
    holidays.add(easter + timedelta(days=39))   # Ascension
    holidays.add(easter + timedelta(days=50))   # Lundi de Pentecôte
    holidays.add(date(year, 9, (21 - date(year, 9, 1).weekday()) % 7 + 15))  # Jeûne fédéral
    return holidays


# ── Build the LLM prompt ──────────────────────────────────────

def _build_prompt(
    target_year: int,
    target_month: int,
    employees: List[Dict],
    shifts: List[Dict],
    manual_entries: List[Dict],
    preferences: List[Dict],
    cfg: Dict[str, Any],
) -> str:
    """Build a detailed system+user prompt for Gemini."""

    start_date = date(target_year, target_month, 1)
    end_date = start_date + relativedelta(months=1, days=-1)
    num_days = (end_date - start_date).days + 1
    vaud_holidays = _get_vaud_holidays(target_year)

    # Calendar metadata
    calendar_info = []
    for d in range(1, num_days + 1):
        dd = date(target_year, target_month, d)
        day_type = "holiday" if dd in vaud_holidays else ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"][dd.weekday()]
        calendar_info.append({"day": d, "date": dd.isoformat(), "type": day_type})

    # Shift types (only the ones the solver cares about)
    solver_shifts = []
    for s in shifts:
        solver_shifts.append({
            "code": s["code"],
            "name": s.get("name", s["code"]),
            "gross_minutes": s.get("gross_minutes", 0),
            "is_night": s.get("is_night", False),
        })

    # Employees — include cmhn_starting_balance for CMHN recovery (C13)
    emp_list = []
    for e in employees:
        emp_list.append({
            "id": e["id"],
            "name": f"{e['last_name']} {e['first_name']}",
            "activity_type": e.get("activity_type", "ta"),
            "hierarchy_code": e.get("hierarchy_code", ""),
            "fte_percent": e.get("fte_percent", 100) or 100,
            "cmhn_starting_balance": int(e.get("cmhn_starting_balance") or 0),
        })

    # Already-locked entries (manual/import)
    locked = []
    for entry in manual_entries:
        locked.append({
            "employee_id": entry["personnel_id"],
            "date": entry["entry_date"],
            "shift_code": entry["shift_code"],
        })

    # Preferences
    pref_list = []
    for p in preferences:
        pref_list.append({
            "employee_id": p["personnel_id"],
            "type": p["pref_type"],
            "shift_code": p.get("shift_code"),
            "day_of_week": p.get("day_of_week"),
            "target_date": p.get("target_date"),
            "is_recurring": p.get("is_recurring", False),
        })

    # Config
    cfg_heures = cfg.get("heures", {})
    cfg_nuits = cfg.get("nuits", {})
    cfg_weekends = cfg.get("weekends", {})
    cfg_gardes = cfg.get("gardes", {})

    # Coverage targets
    wd_cov = cfg_gardes.get("weekday", {"AMJP": 4, "AMNP": 4, "AMHS": 2, "R": 2})
    sat_cov = cfg_gardes.get("saturday", {"AMJP": 4, "AMNP": 4, "AMHS": 2, "R": 0})
    sun_cov = cfg_gardes.get("weekend", {"AMJP": 4, "AMNP": 4, "AMHS": 0, "R": 0})
    max_nights = cfg_nuits.get("max_consecutive", 3)
    max_week_h = cfg_heures.get("max_week", 50)
    max_overshoot = cfg_heures.get("max_month_overshoot_pct", 10)
    max_sun = cfg_weekends.get("max_consecutive_sundays", 3)

    # Build explicit coverage string for rules
    def _cov_str(cov: dict) -> str:
        return ", ".join(f"{v}×{k}" for k, v in cov.items() if v > 0)

    problem_data = {
        "month": target_month,
        "year": target_year,
        "num_days": num_days,
        "calendar": calendar_info,
        "shift_types": solver_shifts,
        "employees": emp_list,
        "locked_entries": locked,
        "preferences": pref_list,
        "constraints": {
            "max_week_hours": max_week_h,
            "max_month_overshoot_pct": max_overshoot,
            "max_consecutive_nights": max_nights,
            "rest_after_night": cfg_nuits.get("rest_after_night", True),
            "max_consecutive_sundays": max_sun,
            "weekday_coverage": wd_cov,
            "saturday_coverage": sat_cov,
            "sunday_holiday_coverage": sun_cov,
        },
        "incompatible_next_day": {
            "AMNP": ["AMJP", "AMHS", "A1", "A2", "6FM", "6P1"],
        },
        "rules_summary": [
            # ── HARD CONSTRAINTS (must be satisfied) ──
            "C1 — ONE SHIFT PER DAY: Each employee has exactly ONE shift per day (or REPOS = day off).",
            "C2 — LOCKED ENTRIES: Entries in locked_entries MUST NOT be changed.",
            f"C3 — 11h REST AFTER NIGHT: After AMNP, the NEXT DAY the employee CANNOT be assigned AMJP, AMHS, A1, A2, 6FM, or 6P1. Only REPOS, AMNP, or absence codes are allowed.",
            f"C4 — MAX CONSECUTIVE NIGHTS: No more than {max_nights} consecutive AMNP shifts per employee. After {max_nights} consecutive AMNP, the next day MUST be REPOS.",
            "C5 — cs/sec EMPLOYEES: activity_type 'cs' or 'sec' employees NEVER do operational shifts (AMJP, AMNP, AMHS, R, RS). They also do NOT work weekends (Saturday/Sunday = REPOS).",
            "C5b — HIERARCHY '01': Employees with hierarchy_code='01' CANNOT do AMNP, AMJP, or AMHS. Weekends = REPOS. Mon-Tue = admin shifts (A1 or A2).",
            "C6 — ABSENCE EXCLUSIONS: Employees on absence codes (VA, M, ANP, AP, E, FO9, C, CMHN, QC1, FIN, MAR, CMAT, CPAT, SM, DEC, CNP, COLL, HC, HS, MR, AMBCE) are UNAVAILABLE — do NOT assign operational shifts on those days.",
            "C7 — R RESTRICTION: R (Rapid Responder) can ONLY be assigned to employees with hierarchy_code in ['01','02','04','05','06'].",
            f"C8 — WEEKLY HOURS: Max {max_week_h}h per week (Mon-Sun), proportional to FTE.",
            f"C9 — MONTHLY HOURS: Base = 10400 min × (fte_percent/100), allow +{max_overshoot}% overshoot maximum.",
            "C10 — SD CAPS: Employees with activity_type='ta' (SD/techniciens) have per-day caps: max 2 on AMJP, max 2 on AMNP, max 1 on AMHS.",
            f"C11 — SUNDAY LIMIT: Max {max_sun} consecutive worked Sundays per employee. A Sunday counts as 'worked' if the employee has an operational shift OR had AMNP on Saturday night.",
            "C12 — CMHN RECOVERY: If an employee (not cs/sec) works ≥3 AMNP in the month OR has cmhn_starting_balance > 480 minutes, they MUST have at least 1 CMHN day in the month.",

            # ── COVERAGE (MOST IMPORTANT — treated as hard constraint) ──
            "⚠️ CRITICAL — DAILY COVERAGE IS THE MOST IMPORTANT RULE ⚠️",
            f"WEEKDAY (Mon-Fri, not holiday): exactly {_cov_str(wd_cov)} employees. Count CAREFULLY.",
            f"SATURDAY (not holiday): exactly {_cov_str(sat_cov)} employees. Count CAREFULLY.",
            f"SUNDAY or HOLIDAY: exactly {_cov_str(sun_cov)} employees. Count CAREFULLY.",
            "Before finalising, COUNT the number of employees per shift per day and VERIFY that it matches the coverage targets above. If it does not match, FIX IT.",

            # ── SOFT PREFERENCES (try to satisfy) ──
            "P1 — GROUPED WORK: Prefer grouped work days over isolated single working days.",
            "P2 — EQUITY: Distribute operational shifts (AMJP, AMNP, AMHS, R) PROPORTIONALLY to each employee's fte_percent. An 80% FTE employee should get ~80% of the shifts a 100% FTE gets. Balance AMNP fairly: don't give all nights to the same people.",
            "P3 — PREFERENCES: Respect employee preferences when possible: avoid_shift, prefer_shift, prefer_day_off.",
        ],
    }

    system_prompt = """You are an expert ambulance service shift planner for Sécurité Riviera (Switzerland, Canton de Vaud).
You will receive a JSON object describing the scheduling problem for one month.
Your task is to produce a COMPLETE schedule for ALL employees for ALL days of the month.

CRITICAL OUTPUT FORMAT:
Return ONLY a valid JSON array. No markdown, no explanations, no code fences.
Each element must be: {"employee_id": "<uuid>", "date": "YYYY-MM-DD", "shift_code": "<CODE>"}

Rules:
- Every employee must have exactly ONE entry per day (including REPOS for days off).
- Do NOT include entries for days that already have a locked_entry — those are fixed.
- Only use shift codes that appear in shift_types or: REPOS, VA, C, CMHN.
- The JSON must be parseable. No trailing commas, no comments.
"""

    user_prompt = f"""Here is the scheduling problem:

{json.dumps(problem_data, ensure_ascii=False, indent=2)}

Generate the complete schedule. Output ONLY the JSON array."""

    return system_prompt, user_prompt


# ── Call Gemini & parse ───────────────────────────────────────

def _call_gemini(system_prompt: str, user_prompt: str) -> List[Dict]:
    """Send the prompt to Gemini and parse the JSON response."""
    import logging
    logger = logging.getLogger("gemini_solver")

    _configure_gemini()

    model = genai.GenerativeModel(
        "gemini-2.5-flash",
        system_instruction=system_prompt,
        generation_config=genai.GenerationConfig(
            temperature=0.2,
            max_output_tokens=65536,
            response_mime_type="application/json",
        ),
    )

    response = model.generate_content(user_prompt)

    # Log diagnostics
    try:
        finish_reason = response.candidates[0].finish_reason if response.candidates else "NO_CANDIDATES"
        logger.warning(f"Gemini finish_reason={finish_reason}")
    except Exception:
        logger.warning("Could not read finish_reason")

    raw_text = response.text.strip()
    logger.warning(f"Gemini response length: {len(raw_text)} chars")

    # Parse JSON — handle potential markdown fences
    if raw_text.startswith("```"):
        raw_text = re.sub(r"^```(?:json)?\s*", "", raw_text)
        raw_text = re.sub(r"\s*```$", "", raw_text)

    try:
        entries = json.loads(raw_text)
    except json.JSONDecodeError as e:
        logger.warning(f"JSON parse failed: {e}. Attempting repair...")
        # Try to repair truncated JSON array
        entries = _repair_truncated_json(raw_text)

    if not isinstance(entries, list):
        raise ValueError("Gemini did not return a JSON array.")

    return entries


def _repair_truncated_json(raw_text: str) -> List[Dict]:
    """
    Attempt to repair a truncated JSON array by finding the last complete object.
    e.g. [{"a":1},{"b":2},{"c":  -->  [{"a":1},{"b":2}]
    """
    import logging
    logger = logging.getLogger("gemini_solver")

    # Find the last complete "}" that ends an object in the array
    last_complete = raw_text.rfind("}")
    if last_complete == -1:
        raise ValueError("Cannot repair JSON: no complete object found")

    # Take everything up to and including that "}"
    candidate = raw_text[:last_complete + 1].rstrip().rstrip(",")

    # Ensure it starts with "["
    if not candidate.lstrip().startswith("["):
        candidate = "[" + candidate

    # Close the array
    candidate = candidate + "]"

    try:
        entries = json.loads(candidate)
        logger.warning(f"JSON repair succeeded: {len(entries)} entries recovered")
        return entries
    except json.JSONDecodeError as e2:
        raise ValueError(f"JSON repair also failed: {e2}. First 500 chars: {raw_text[:500]}")


# ── Validate entries ──────────────────────────────────────────

def _validate_entries(
    entries: List[Dict],
    employees: List[Dict],
    shifts: List[Dict],
    manual_entries: List[Dict],
    target_year: int,
    target_month: int,
) -> Tuple[List[Dict], List[str]]:
    """
    Validate Gemini output. Returns (valid_entries, warnings).
    Drops invalid rows but keeps good ones.
    """
    start_date = date(target_year, target_month, 1)
    end_date = start_date + relativedelta(months=1, days=-1)

    emp_ids = {e["id"] for e in employees}
    shift_codes = {s["code"] for s in shifts} | {"REPOS", "VA", "C", "CMHN", "QC1", "E", "M",
                                                   "ANP", "AP", "FO9", "FIN", "MAR", "CMAT",
                                                   "CPAT", "SM", "DEC", "CNP", "COLL"}
    locked_keys = {(e["personnel_id"], e["entry_date"]) for e in manual_entries}

    valid = []
    warnings = []
    seen = set()  # (employee_id, date) dedup

    for i, entry in enumerate(entries):
        eid = entry.get("employee_id", "")
        d_str = entry.get("date", "")
        code = entry.get("shift_code", "")

        # Basic field validation
        if not eid or not d_str or not code:
            warnings.append(f"Row {i}: missing field(s)")
            continue

        if eid not in emp_ids:
            warnings.append(f"Row {i}: unknown employee {eid[:8]}…")
            continue

        # Parse date
        try:
            dd = date.fromisoformat(d_str)
        except (ValueError, TypeError):
            warnings.append(f"Row {i}: bad date '{d_str}'")
            continue

        if dd < start_date or dd > end_date:
            warnings.append(f"Row {i}: date {d_str} out of month range")
            continue

        if code not in shift_codes:
            warnings.append(f"Row {i}: unknown shift code '{code}'")
            continue

        # Skip locked days
        if (eid, d_str) in locked_keys:
            continue  # silently skip — locked entries are immutable

        # Skip REPOS (we only persist actual shifts)
        if code == "REPOS":
            continue

        # Dedup
        key = (eid, d_str)
        if key in seen:
            warnings.append(f"Row {i}: duplicate {eid[:8]}… on {d_str}")
            continue
        seen.add(key)

        valid.append({
            "personnel_id": eid,
            "entry_date": d_str,
            "shift_code": code,
            "source": "solver",
            "is_locked": False,
        })

    return valid, warnings


# ── Coverage validation & repair (Phase 2) ────────────────────

def _validate_and_repair_coverage(
    entries: List[Dict],
    employees: List[Dict],
    manual_entries: List[Dict],
    target_year: int,
    target_month: int,
    cfg: Dict[str, Any],
) -> Tuple[List[Dict], List[str]]:
    """
    Deterministic repair pass that fixes under/over-staffing and
    constraint violations in the Gemini output.
    Returns (repaired_entries, repair_warnings).
    """
    import logging
    logger = logging.getLogger("gemini_solver")

    start_date = date(target_year, target_month, 1)
    end_date = start_date + relativedelta(months=1, days=-1)
    num_days = (end_date - start_date).days + 1
    vaud_holidays = _get_vaud_holidays(target_year)

    cfg_gardes = cfg.get("gardes", {})
    wd_cov = cfg_gardes.get("weekday", {"AMJP": 4, "AMNP": 4, "AMHS": 2, "R": 2})
    sat_cov = cfg_gardes.get("saturday", {"AMJP": 4, "AMNP": 4, "AMHS": 2, "R": 0})
    sun_cov = cfg_gardes.get("weekend", {"AMJP": 4, "AMNP": 4, "AMHS": 0, "R": 0})

    # Build indexed structures
    locked_keys = {(e["personnel_id"], e["entry_date"]) for e in manual_entries}
    locked_shifts = {}  # (eid, date_str) -> shift_code from locked
    for me in manual_entries:
        locked_shifts[(me["personnel_id"], me["entry_date"])] = me["shift_code"]

    # Solver entries indexed by (eid, date_str)
    entry_map: Dict[Tuple[str, str], Dict] = {}
    for e in entries:
        entry_map[(e["personnel_id"], e["entry_date"])] = e

    emp_by_id = {e["id"]: e for e in employees}
    rr_allowed = {"01", "02", "04", "05", "06"}

    absence_codes = {"VA", "M", "ANP", "AP", "E", "FO9", "C", "CMHN", "QC1", "FIN",
                     "MAR", "CMAT", "CPAT", "SM", "DEC", "CNP", "COLL", "HC", "HS",
                     "MR", "AMBCE", "QFO"}
    incompatible_after_amnp = {"AMJP", "AMHS", "A1", "A2", "6FM", "6P1"}

    warnings = []

    # ── Pass 1: Fix individual constraint violations ──────────
    for d in range(1, num_days + 1):
        dd = date(target_year, target_month, d)
        d_str = dd.isoformat()
        prev_d_str = (dd - timedelta(days=1)).isoformat() if d > 1 else None

        for emp in employees:
            eid = emp["id"]
            key = (eid, d_str)
            if key in locked_keys:
                continue  # never touch locked

            entry = entry_map.get(key)
            if not entry:
                continue

            code = entry["shift_code"]
            act_type = emp.get("activity_type", "ta")
            h_code = emp.get("hierarchy_code", "")

            # C5: cs/sec cannot do operational shifts
            if act_type in ("cs", "sec") and code in ("AMJP", "AMNP", "AMHS", "R", "RS", "AMBCE"):
                warnings.append(f"Repair C5: {emp.get('last_name','')} cs/sec on {code} day {d} → removed")
                del entry_map[key]
                continue

            # C5: cs/sec no weekends
            if act_type in ("cs", "sec") and dd.weekday() >= 5 and code not in absence_codes:
                warnings.append(f"Repair C5: {emp.get('last_name','')} cs/sec weekend {d} → removed")
                del entry_map[key]
                continue

            # C5b: hierarchy 01 restrictions
            if h_code == "01" and code in ("AMNP", "AMJP", "AMHS"):
                warnings.append(f"Repair C5b: {emp.get('last_name','')} h01 on {code} day {d} → removed")
                del entry_map[key]
                continue

            # C7: R restriction
            if code == "R" and h_code not in rr_allowed:
                warnings.append(f"Repair C7: {emp.get('last_name','')} R not allowed (h={h_code}) day {d} → removed")
                del entry_map[key]
                continue

            # C3: Forbidden combo after AMNP
            if prev_d_str and code in incompatible_after_amnp:
                prev_locked = locked_shifts.get((eid, prev_d_str))
                prev_solver = entry_map.get((eid, prev_d_str))
                prev_code = prev_locked or (prev_solver["shift_code"] if prev_solver else None)
                if prev_code == "AMNP":
                    warnings.append(f"Repair C3: {emp.get('last_name','')} {code} after AMNP day {d} → removed")
                    del entry_map[key]
                    continue

    # ── Pass 2: Fix coverage (under/over staffing) ────────────
    for d in range(1, num_days + 1):
        dd = date(target_year, target_month, d)
        d_str = dd.isoformat()

        # Determine day type → coverage target
        if dd in vaud_holidays or dd.weekday() == 6:
            target_cov = sun_cov
        elif dd.weekday() == 5:
            target_cov = sat_cov
        else:
            target_cov = wd_cov

        for shift_code, required in target_cov.items():
            if required <= 0:
                continue

            # Count current assignments (locked + solver)
            assigned = []
            for emp in employees:
                eid = emp["id"]
                lk = locked_shifts.get((eid, d_str))
                sv = entry_map.get((eid, d_str))
                actual_code = lk or (sv["shift_code"] if sv else None)
                if actual_code == shift_code:
                    assigned.append(eid)

            current = len(assigned)

            # Under-staffed → add eligible employees
            if current < required:
                deficit = required - current
                # Find eligible: not locked, not already assigned, not on absence, passes constraints
                candidates = []
                for emp in employees:
                    eid = emp["id"]
                    if (eid, d_str) in locked_keys:
                        continue
                    if (eid, d_str) in entry_map:
                        continue  # already has something
                    act_type = emp.get("activity_type", "ta")
                    h_code = emp.get("hierarchy_code", "")

                    # Skip cs/sec for operational shifts
                    if act_type in ("cs", "sec") and shift_code in ("AMJP", "AMNP", "AMHS", "R"):
                        continue
                    # Skip R for non-allowed hierarchies
                    if shift_code == "R" and h_code not in rr_allowed:
                        continue
                    # Skip h01 for AMNP/AMJP/AMHS
                    if h_code == "01" and shift_code in ("AMNP", "AMJP", "AMHS"):
                        continue
                    # Skip cs/sec on weekends
                    if act_type in ("cs", "sec") and dd.weekday() >= 5:
                        continue
                    # C3: check if previous day was AMNP
                    if shift_code in incompatible_after_amnp and d > 1:
                        prev_d_str = (dd - timedelta(days=1)).isoformat()
                        prev_code = locked_shifts.get((eid, prev_d_str))
                        if not prev_code:
                            prev_sv = entry_map.get((eid, prev_d_str))
                            prev_code = prev_sv["shift_code"] if prev_sv else None
                        if prev_code == "AMNP":
                            continue

                    candidates.append(emp)

                # Sort by FTE (higher FTE = more available)
                candidates.sort(key=lambda e: -(e.get("fte_percent") or 100))

                for emp in candidates[:deficit]:
                    entry_map[(emp["id"], d_str)] = {
                        "personnel_id": emp["id"],
                        "entry_date": d_str,
                        "shift_code": shift_code,
                        "source": "solver",
                        "is_locked": False,
                    }
                    warnings.append(f"Repair coverage: added {emp.get('last_name','')} to {shift_code} on day {d}")

            # Over-staffed → remove surplus (lowest FTE first)
            elif current > required:
                surplus = current - required
                # Only remove from solver entries, not locked
                removable = []
                for eid in assigned:
                    if (eid, d_str) not in locked_keys and (eid, d_str) in entry_map:
                        emp = emp_by_id.get(eid)
                        removable.append((eid, emp.get("fte_percent", 100) if emp else 100))
                # Remove lowest-FTE first
                removable.sort(key=lambda x: x[1])
                for eid, _ in removable[:surplus]:
                    del entry_map[(eid, d_str)]
                    emp = emp_by_id.get(eid)
                    warnings.append(f"Repair coverage: removed {emp.get('last_name','') if emp else eid[:8]} from {shift_code} on day {d}")

    # Convert back to list
    repaired = list(entry_map.values())
    logger.warning(f"Coverage repair: {len(entries)} → {len(repaired)} entries, {len(warnings)} repairs")
    return repaired, warnings


# ── Constraint scoring (Phase 3+4) ────────────────────────────

def _score_violations(
    entries: List[Dict],
    employees: List[Dict],
    manual_entries: List[Dict],
    target_year: int,
    target_month: int,
    cfg: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Score constraint compliance. Returns a report dict:
    {
        coverage_score: float (0-1),
        constraint_violations: [...],
        critical_count: int,
        warning_count: int,
    }
    """
    start_date = date(target_year, target_month, 1)
    end_date = start_date + relativedelta(months=1, days=-1)
    num_days = (end_date - start_date).days + 1
    vaud_holidays = _get_vaud_holidays(target_year)

    cfg_gardes = cfg.get("gardes", {})
    wd_cov = cfg_gardes.get("weekday", {"AMJP": 4, "AMNP": 4, "AMHS": 2, "R": 2})
    sat_cov = cfg_gardes.get("saturday", {"AMJP": 4, "AMNP": 4, "AMHS": 2, "R": 0})
    sun_cov = cfg_gardes.get("weekend", {"AMJP": 4, "AMNP": 4, "AMHS": 0, "R": 0})

    # Build lookup: date_str -> {shift_code -> count}
    locked_shifts = {}
    for me in manual_entries:
        locked_shifts[(me["personnel_id"], me["entry_date"])] = me["shift_code"]

    entry_map = {}
    for e in entries:
        entry_map[(e["personnel_id"], e["entry_date"])] = e["shift_code"]

    violations = []
    total_checks = 0
    total_ok = 0

    for d in range(1, num_days + 1):
        dd = date(target_year, target_month, d)
        d_str = dd.isoformat()

        if dd in vaud_holidays or dd.weekday() == 6:
            target_cov = sun_cov
        elif dd.weekday() == 5:
            target_cov = sat_cov
        else:
            target_cov = wd_cov

        for shift_code, required in target_cov.items():
            if required <= 0:
                continue

            total_checks += 1
            count = 0
            for emp in employees:
                eid = emp["id"]
                c = locked_shifts.get((eid, d_str)) or entry_map.get((eid, d_str))
                if c == shift_code:
                    count += 1

            if count == required:
                total_ok += 1
            else:
                violations.append({
                    "type": "coverage",
                    "day": d_str,
                    "shift": shift_code,
                    "expected": required,
                    "actual": count,
                    "severity": "critical" if abs(count - required) >= 2 else "warning",
                })

    # C3: forbidden combo check
    incompatible_after_amnp = {"AMJP", "AMHS", "A1", "A2", "6FM", "6P1"}
    for emp in employees:
        eid = emp["id"]
        for d in range(1, num_days):
            dd = date(target_year, target_month, d)
            d_str = dd.isoformat()
            next_d_str = (dd + timedelta(days=1)).isoformat()
            cur = locked_shifts.get((eid, d_str)) or entry_map.get((eid, d_str))
            nxt = locked_shifts.get((eid, next_d_str)) or entry_map.get((eid, next_d_str))
            if cur == "AMNP" and nxt in incompatible_after_amnp:
                violations.append({
                    "type": "forbidden_combo",
                    "employee": f"{emp.get('last_name','')} {emp.get('first_name','')}",
                    "day1": d_str,
                    "day2": next_d_str,
                    "severity": "critical",
                })

    crit = sum(1 for v in violations if v.get("severity") == "critical")
    warn = sum(1 for v in violations if v.get("severity") == "warning")
    cov_score = total_ok / total_checks if total_checks > 0 else 0.0

    return {
        "coverage_score": round(cov_score, 3),
        "constraint_violations": violations[:30],  # cap for response size
        "critical_count": crit,
        "warning_count": warn,
    }


def _build_retry_prompt(violations: Dict[str, Any], original_user_prompt: str) -> str:
    """
    Build a correction prompt that tells Gemini exactly what went wrong.
    """
    lines = ["Your previous schedule had the following violations that MUST be fixed:\n"]
    for v in violations.get("constraint_violations", [])[:15]:
        if v["type"] == "coverage":
            lines.append(f"- Day {v['day']}: {v['shift']} has {v['actual']} employees but needs exactly {v['expected']}.")
        elif v["type"] == "forbidden_combo":
            lines.append(f"- {v['employee']}: AMNP on {v['day1']} followed by early shift on {v['day2']} (FORBIDDEN).")
        else:
            lines.append(f"- {v['type']}: {v}")

    lines.append(f"\nCoverage score: {violations['coverage_score']} (1.0 = perfect).")
    lines.append("\nFix ALL violations above and return the COMPLETE corrected schedule as a JSON array.")
    lines.append("Do NOT change entries that are already correct — only fix the violated days/employees.")
    lines.append("\n" + original_user_prompt)
    return "\n".join(lines)


# ── Public API ────────────────────────────────────────────────

def solve_with_gemini(
    target_year: int,
    target_month: int,
    supabase_client: Any,
    load_config_fn: Any,
) -> dict:
    """
    Full Gemini solver pipeline:
    1. Load data from Supabase
    2. Build prompt
    3. Call Gemini (with up to 2 retries on violations)
    4. Validate + repair coverage
    5. Score compliance
    6. Write to DB
    """
    import logging
    from collections import defaultdict

    logger = logging.getLogger("gemini_solver")

    # ── Load config ─────────────────────────────────────────
    cfg = load_config_fn()

    # ── Load data ───────────────────────────────────────────
    start_date = date(target_year, target_month, 1)
    end_date = start_date + relativedelta(months=1, days=-1)

    personnel_res = supabase_client.table("personnel").select("*").eq("is_active", True).execute()
    shifts_res = supabase_client.table("shift_types").select("*").execute()
    employees = personnel_res.data
    shifts = shifts_res.data

    # Preferences
    try:
        prefs_res = supabase_client.table("employee_preferences").select("*").execute()
        all_prefs = prefs_res.data or []
    except Exception:
        all_prefs = []

    # Manual/import entries (locked)
    manual_res = supabase_client.table("schedule_entries")\
        .select("*")\
        .gte("entry_date", start_date.isoformat())\
        .lte("entry_date", end_date.isoformat())\
        .in_("source", ["manual", "import"])\
        .execute()
    manual_entries = manual_res.data or []

    # ── Build prompt ────────────────────────────────────────
    system_prompt, user_prompt = _build_prompt(
        target_year, target_month,
        employees, shifts, manual_entries, all_prefs, cfg,
    )

    # ── Call Gemini (retry loop) ─────────────────────────────
    MAX_ATTEMPTS = 3
    all_warnings = []
    best_entries = []
    best_score = None

    for attempt in range(MAX_ATTEMPTS):
        logger.warning(f"Gemini attempt {attempt + 1}/{MAX_ATTEMPTS}")

        try:
            raw_entries = _call_gemini(system_prompt, user_prompt)
        except Exception as e:
            logger.warning(f"Gemini call failed on attempt {attempt + 1}: {e}")
            all_warnings.append(f"Attempt {attempt + 1} failed: {str(e)[:100]}")
            continue

        # ── Validate ─────────────────────────────────────────
        valid_entries, val_warnings = _validate_entries(
            raw_entries, employees, shifts, manual_entries,
            target_year, target_month,
        )
        all_warnings.extend(val_warnings)

        if not valid_entries:
            all_warnings.append(f"Attempt {attempt + 1}: no valid entries")
            continue

        # ── Coverage repair ──────────────────────────────────
        repaired, repair_warnings = _validate_and_repair_coverage(
            valid_entries, employees, manual_entries,
            target_year, target_month, cfg,
        )
        all_warnings.extend(repair_warnings)

        # ── Score ────────────────────────────────────────────
        score = _score_violations(
            repaired, employees, manual_entries,
            target_year, target_month, cfg,
        )
        logger.warning(
            f"Attempt {attempt + 1}: coverage={score['coverage_score']}, "
            f"critical={score['critical_count']}, warning={score['warning_count']}"
        )

        # Keep best result
        if best_score is None or score["coverage_score"] > best_score["coverage_score"]:
            best_entries = repaired
            best_score = score

        # Good enough? Stop retrying
        if score["critical_count"] == 0:
            logger.warning(f"No critical violations — stopping after attempt {attempt + 1}")
            break

        # Build retry prompt for next attempt
        if attempt < MAX_ATTEMPTS - 1:
            user_prompt = _build_retry_prompt(score, user_prompt)

    if not best_entries:
        return {
            "status": "error",
            "message": "Gemini n'a produit aucune entrée valide après plusieurs tentatives.",
            "warnings": all_warnings[:30],
        }

    valid_entries = best_entries
    compliance = best_score

    # ── Write to DB ─────────────────────────────────────────
    # Delete old solver entries for the month
    supabase_client.table("schedule_entries")\
        .delete()\
        .gte("entry_date", start_date.isoformat())\
        .lte("entry_date", end_date.isoformat())\
        .eq("source", "solver")\
        .execute()

    # Batch upsert
    for i in range(0, len(valid_entries), 500):
        batch = valid_entries[i:i + 500]
        supabase_client.table("schedule_entries").upsert(
            batch, on_conflict="personnel_id,entry_date"
        ).execute()

    # ── Build response schedule ─────────────────────────────
    emp_by_id = {e["id"]: e for e in employees}
    schedule = []
    num_days = (end_date - start_date).days + 1
    entry_by_day: Dict[str, List[Dict]] = defaultdict(list)
    for entry in valid_entries:
        entry_by_day[entry["entry_date"]].append(entry)

    for d in range(1, num_days + 1):
        dd = date(target_year, target_month, d)
        d_str = dd.isoformat()
        day_sched = {"date": d_str, "shifts": {}}
        for s_code in ["AMJP", "AMNP", "AMHS", "R", "CMHN"]:
            staff = []
            for e in entry_by_day.get(d_str, []):
                if e["shift_code"] == s_code and e["personnel_id"] in emp_by_id:
                    emp = emp_by_id[e["personnel_id"]]
                    staff.append({
                        "name": f"{emp['last_name']} {emp['first_name']}",
                        "id": emp["id"],
                    })
            day_sched["shifts"][s_code] = staff
        schedule.append(day_sched)

    return {
        "status": "success",
        "solver_status": "gemini-2.5-flash",
        "entries_saved": len(valid_entries),
        "warnings": all_warnings[:30] if all_warnings else [],
        "schedule": schedule,
        "compliance": compliance,
        "message": (
            f"Gemini AI a généré {len(valid_entries)} entrées "
            f"(couverture: {compliance['coverage_score']*100:.0f}%, "
            f"{compliance['critical_count']} violations critiques, "
            f"{compliance['warning_count']} avertissements)."
        ),
    }
