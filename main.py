from fastapi import FastAPI, UploadFile, File, Form, Depends, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Dict, Any, Optional
from ortools.sat.python import cp_model
import os
from supabase import create_client, Client
from datetime import date, datetime, timedelta
from dateutil.relativedelta import relativedelta
from collections import defaultdict
import tempfile
import json
import uuid as uuid_mod
import hashlib
import time

# We assume extractor.py is alongside this file
try:
    import extractor
except ImportError:
    pass

app = FastAPI(title="ASR Solver API")

# ─── API Key Authentication ──────────────────────────────────
SOLVER_API_KEY = os.environ.get("SOLVER_API_KEY", "")


async def verify_api_key(x_api_key: str = Header(default="")):
    """Validate the X-API-Key header against SOLVER_API_KEY env var."""
    if not SOLVER_API_KEY:
        return  # No key configured = open access (dev mode)
    if x_api_key != SOLVER_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


# Add CORS middleware — restricted to dashboard origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://asr-planification-dashboard.vercel.app",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Setup Supabase client
SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://dcijgpmpysyfcjeerxqn.supabase.co")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

if not SUPABASE_KEY:
    try:
        with open("../asr-planification-dashboard/.env.local") as f:
            for line in f:
                if "NEXT_PUBLIC_SUPABASE_ANON_KEY" in line:
                    SUPABASE_KEY = line.split("=", 1)[1].strip().strip('"').strip("'")
    except Exception:
        pass

if not SUPABASE_KEY:
    raise RuntimeError(
        "SUPABASE_KEY is not set. Set SUPABASE_KEY env var or create "
        "../asr-planification-dashboard/.env.local with NEXT_PUBLIC_SUPABASE_ANON_KEY."
    )

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# ─── In-memory state for last solver run ───────────────────
_last_run: Dict[str, Any] = {"status": "idle", "message": "No solver run yet."}

# ─── In-memory pending imports for validate→confirm flow ──
# token → {payload, summary, validation, expires}
_pending_imports: Dict[str, Dict[str, Any]] = {}


# ─── Config Loader ─────────────────────────────────────────
def load_config() -> Dict[str, Any]:
    """Load app_config from Supabase, fallback to defaults."""
    defaults = {
        "heures": {"max_week": 50, "max_month_overshoot_pct": 10, "dressing_minutes": 10},
        "nuits": {"max_consecutive": 3, "rest_after_night": True, "cmhn_rate_pct": 15},
        "weekends": {"max_consecutive_sundays": 3, "cs_sec_exempt": True},
        "gardes": {
            "weekday": {"AMJP": 4, "AMNP": 4, "AMHS": 2, "R": 2},
            "saturday": {"AMJP": 4, "AMNP": 4, "AMHS": 2, "R": 0},
            "weekend": {"AMJP": 4, "AMNP": 4, "AMHS": 0, "R": 0},
            "min_sd_per_shift": 1,
        },
        "vacances": {"weekend_before": True, "weekend_after": True, "max_simultaneous": 5},
        "etudiants": {"default_fte": 50, "trinome_interval": 9},
        "equite": {"weights": {"heures": 1, "nuits": 1, "weekends": 1, "gardes": 1}, "history_months": 3},
    }
    try:
        res = supabase.table("app_config").select("key, value").execute()
        if res.data:
            for row in res.data:
                defaults[row["key"]] = row["value"]
    except Exception:
        pass  # Use defaults if table doesn't exist yet
    return defaults


# ─── Vaud holidays ─────────────────────────────────────────
def get_vaud_holidays(year: int) -> set:
    holidays = {
        date(year, 1, 1), date(year, 1, 2),
        date(year, 8, 1), date(year, 12, 25),
    }
    if year == 2026:
        holidays.update({
            date(2026, 4, 3), date(2026, 4, 6),
            date(2026, 5, 14), date(2026, 5, 25),
            date(2026, 9, 21),
        })
    return holidays


# ─── Models ────────────────────────────────────────────────
class SolverRequest(BaseModel):
    year: int
    month: int


# ═══════════════════════════════════════════════════════════
# ENDPOINTS
# ═══════════════════════════════════════════════════════════

@app.get("/")
def read_root():
    return {"status": "ok", "message": "ASR Solver API is running"}


@app.get("/config", dependencies=[Depends(verify_api_key)])
def get_config():
    """Return current app_config values."""
    config = load_config()
    return {"status": "ok", "config": config}


@app.get("/status")
def get_status():
    """Return last solver run status."""
    return _last_run


@app.post("/clear", dependencies=[Depends(verify_api_key)])
def clear_solver(request: SolverRequest):
    """Delete all solver-generated entries for the given month/year."""
    start_date = date(request.year, request.month, 1)
    end_date = start_date + relativedelta(months=1, days=-1)

    result = supabase.table("schedule_entries")\
        .delete()\
        .gte("entry_date", start_date.isoformat())\
        .lte("entry_date", end_date.isoformat())\
        .eq("source", "solver")\
        .execute()

    deleted = len(result.data) if result.data else 0
    return {
        "status": "success",
        "message": f"{deleted} entrées solver supprimées pour {start_date.strftime('%B %Y')}.",
        "deleted": deleted,
    }


@app.post("/solve", dependencies=[Depends(verify_api_key)])
def run_solver(request: SolverRequest):
    global _last_run
    _last_run = {"status": "running", "message": f"Solving {request.month}/{request.year}…",
                 "started_at": datetime.now().isoformat()}

    try:
        result = _do_solve(request.year, request.month)
        _last_run = {
            "status": result["status"],
            "message": result.get("message", "Done"),
            "entries_saved": result.get("entries_saved", 0),
            "penalty": result.get("penalty", 0),
            "finished_at": datetime.now().isoformat(),
        }
        return result
    except Exception as exc:
        _last_run = {"status": "error", "message": str(exc),
                     "finished_at": datetime.now().isoformat()}
        return {"status": "error", "message": str(exc)}


def _do_solve(target_year: int, target_month: int) -> dict:
    """Core solver logic with config-driven constraints and equity."""

    # ─── Load config ───────────────────────────────────────
    cfg = load_config()
    cfg_heures = cfg["heures"]
    cfg_nuits = cfg["nuits"]
    cfg_weekends = cfg["weekends"]
    cfg_gardes = cfg["gardes"]

    max_week_minutes = int(cfg_heures["max_week"] * 60)  # 50h → 3000 min
    max_month_overshoot_pct = cfg_heures.get("max_month_overshoot_pct", 10)
    max_consecutive_nights = cfg_nuits.get("max_consecutive", 3)
    max_consecutive_sundays = cfg_weekends.get("max_consecutive_sundays", 3)
    weekday_needs = cfg_gardes.get("weekday", {"AMJP": 4, "AMNP": 4, "AMHS": 2, "R": 2})
    saturday_needs = cfg_gardes.get("saturday", {"AMJP": 4, "AMNP": 4, "AMHS": 2, "R": 0})
    sunday_needs = cfg_gardes.get("weekend", {"AMJP": 4, "AMNP": 4, "AMHS": 0, "R": 0})

    # ─── Data loading ──────────────────────────────────────
    start_date = date(target_year, target_month, 1)
    end_date = start_date + relativedelta(months=1, days=-1)
    num_days = (end_date - start_date).days + 1
    days_range = range(1, num_days + 1)

    def get_date(day_int):
        return date(target_year, target_month, day_int)

    personnel_res = supabase.table("personnel").select("*").eq("is_active", True).execute()
    shifts_res = supabase.table("shift_types").select("*").execute()
    employees = personnel_res.data
    shifts = shifts_res.data

    shift_by_code = {s['code']: s for s in shifts}
    shift_codes = list(shift_by_code.keys()) + ["REPOS"]
    emp_ids = [e['id'] for e in employees]
    emp_by_id = {e['id']: e for e in employees}

    # ─── Employee preferences ──────────────────────────────
    try:
        prefs_res = supabase.table("employee_preferences").select("*").execute()
        all_prefs = prefs_res.data or []
    except Exception:
        all_prefs = []

    def pref_matching_days(pref):
        """Return list of day-ints (1..num_days) that match this preference."""
        matched = []
        if pref.get("is_recurring") and pref.get("day_of_week") is not None:
            dow = pref["day_of_week"]  # 0=Mon..6=Sun
            for d in days_range:
                if get_date(d).weekday() == dow:
                    matched.append(d)
        elif pref.get("target_date"):
            td = date.fromisoformat(pref["target_date"])
            if td.year == target_year and td.month == target_month:
                matched.append(td.day)
        return matched

    # Manual/import entries
    manual_entries_res = supabase.table("schedule_entries")\
        .select("*")\
        .gte("entry_date", start_date.isoformat())\
        .lte("entry_date", end_date.isoformat())\
        .in_("source", ["manual", "import"])\
        .execute()

    manual_entries = manual_entries_res.data
    manual_map = {}
    for entry in manual_entries:
        d = date.fromisoformat(entry['entry_date']).day
        manual_map[(entry['personnel_id'], d)] = entry['shift_code']

    # T-1 history
    last_day_prev = start_date - timedelta(days=1)
    try:
        history_res = supabase.table("schedule_entries")\
            .select("personnel_id, shift_code")\
            .eq("entry_date", last_day_prev.isoformat())\
            .execute()
        history_map = {row['personnel_id']: row['shift_code'] for row in history_res.data}
    except Exception:
        history_map = {}

    # ─── CP-SAT Model ─────────────────────────────────────
    model = cp_model.CpModel()

    X = {}
    for e in emp_ids:
        for d in days_range:
            for s in shift_codes:
                X[(e, d, s)] = model.NewBoolVar(f"shift_{e}_{d}_{s}")

    # ═══ HARD CONSTRAINTS ═══

    # H-PREF: Unavailable dates → force REPOS
    for pref in all_prefs:
        if pref["pref_type"] != "unavailable":
            continue
        eid = pref["personnel_id"]
        if eid not in emp_by_id:
            continue
        for d in pref_matching_days(pref):
            if (eid, d) not in manual_map:
                model.Add(X[(eid, d, "REPOS")] == 1)

    # C1: Uniqueness — exactly 1 shift per employee per day
    for e in emp_ids:
        for d in days_range:
            model.AddExactlyOne([X[(e, d, s)] for s in shift_codes])

    # C2: Pre-assignment — lock manual/import entries
    for (e, d), s_code in manual_map.items():
        if s_code in shift_codes:
            model.Add(X[(e, d, s_code)] == 1)

    # C3: 11h rest after night — no day shift after AMNP
    incompatible_after_night = ["AMJP", "AMHS", "A1", "A2", "6FM", "6P1"]
    for e in emp_ids:
        # T-1 continuity
        if history_map.get(e) == "AMNP":
            for next_s in incompatible_after_night:
                if next_s in shift_codes:
                    model.Add(X[(e, 1, next_s)] == 0)
        # Intra-month
        for d in range(1, num_days):
            for next_s in incompatible_after_night:
                if next_s in shift_codes:
                    model.AddImplication(X[(e, d, "AMNP")], X[(e, d + 1, next_s)].Not())

    # C4: Max consecutive nights (configurable, default 3)
    if "AMNP" in shift_codes:
        window = max_consecutive_nights + 1
        for e in emp_ids:
            for d_start in range(1, num_days - window + 2):
                d_end = min(d_start + window - 1, num_days)
                days_window = list(range(d_start, d_end + 1))
                if len(days_window) > max_consecutive_nights:
                    model.Add(sum(X[(e, d, "AMNP")] for d in days_window) <= max_consecutive_nights)

    # C5: Direction rules (cs/sec)
    operational_shifts = ["AMJP", "AMNP", "AMHS", "R", "RS", "AMBCE"]
    for e in employees:
        eid = e['id']
        act_type = e['activity_type']
        h_code = e['hierarchy_code']

        if act_type in ("cs", "sec"):
            for d in days_range:
                if (eid, d) in manual_map:
                    continue
                for s in operational_shifts:
                    if s in shift_codes:
                        model.Add(X[(eid, d, s)] == 0)
                if get_date(d).weekday() >= 5:
                    for s in shift_codes:
                        if s != "REPOS":
                            model.Add(X[(eid, d, s)] == 0)

        # Hybride Levet (01)
        if h_code == "01":
            for d in days_range:
                if (eid, d) in manual_map:
                    continue
                for s in ["AMNP", "AMJP", "AMHS"]:
                    if s in shift_codes:
                        model.Add(X[(eid, d, s)] == 0)
                if get_date(d).weekday() >= 5:
                    for s in shift_codes:
                        if s != "REPOS":
                            model.Add(X[(eid, d, s)] == 0)
                elif get_date(d).weekday() in (0, 1):
                    admin_shifts = [s for s in ["A1", "A2"] if s in shift_codes]
                    if admin_shifts:
                        model.Add(sum(X[(eid, d, s)] for s in admin_shifts) == 1)

    # C6: Student-Reference pairs
    for e_student in employees:
        if e_student.get('reference_rescuer_id'):
            ref_id = e_student['reference_rescuer_id']
            eid_student = e_student['id']
            if ref_id in emp_ids:
                for d in days_range:
                    for s in ["AMJP", "AMNP", "AMHS", "R"]:
                        if s in shift_codes:
                            model.Add(X[(eid_student, d, s)] == X[(ref_id, d, s)])

    # C7: Weekly 50h LTr limit (config-driven, proportional to FTE)
    weeks = defaultdict(list)
    for d in days_range:
        iso = get_date(d).isocalendar()
        weeks[(iso[0], iso[1])].append(d)

    for week_key, days_in_week in weeks.items():
        for e in emp_ids:
            fte = emp_by_id[e].get('fte_percent') or 100
            limit = int(max_week_minutes * fte / 100)
            week_min = []
            for d in days_in_week:
                for s in shift_codes:
                    if s != "REPOS" and s in shift_by_code:
                        dur = shift_by_code[s].get("gross_minutes") or 0
                        if dur > 0:
                            week_min.append(X[(e, d, s)] * dur)
            if week_min:
                model.Add(sum(week_min) <= limit)

    # C8: Monthly limit (contract + overshoot %)
    for e in employees:
        eid = e['id']
        fte = e.get('fte_percent', 100) or 100
        monthly_contract_min = 10400 * (fte / 100.0)
        monthly_limit = int(monthly_contract_min * (1 + max_month_overshoot_pct / 100))

        month_min = []
        for d in days_range:
            for s in shift_codes:
                if s != "REPOS" and s in shift_by_code:
                    dur = shift_by_code[s].get("gross_minutes") or 0
                    if dur > 0:
                        month_min.append(X[(eid, d, s)] * dur)
        if month_min:
            model.Add(sum(month_min) <= monthly_limit)

    # C9: Daily coverage requirements (config-driven)
    vaud_holidays = get_vaud_holidays(target_year)
    absence_codes = {"VA", "M", "ANP", "AP", "E", "FO9", "C", "CMHN", "QC1", "FIN",
                     "MAR", "CMAT", "CPAT", "SM", "DEC", "CNP", "COLL"}

    for d in days_range:
        dd = get_date(d)
        is_saturday = dd.weekday() == 5
        is_sunday = dd.weekday() == 6
        is_holiday = dd in vaud_holidays

        if is_holiday or is_sunday:
            needs = sunday_needs
        elif is_saturday:
            needs = saturday_needs
        else:
            needs = weekday_needs

        locked_out = sum(1 for e in emp_ids if manual_map.get((e, d)) in absence_codes)
        available_pool = len(emp_ids) - locked_out

        for s_code, required in needs.items():
            if s_code not in shift_codes or required == 0:
                continue
            cap = min(required, max(0, available_pool - required))
            model.Add(sum(X[(e, d, s_code)] for e in emp_ids) >= cap)
            model.Add(sum(X[(e, d, s_code)] for e in emp_ids) <= required)

    # C10: SD (technicien) caps per shift type per day
    sd_emp_ids = [e['id'] for e in employees if e['activity_type'] == 'ta']
    for d in days_range:
        for s, mx in [("AMJP", 2), ("AMNP", 2), ("AMHS", 1)]:
            if s in shift_codes:
                model.Add(sum(X[(eid, d, s)] for eid in sd_emp_ids) <= mx)

    # C11: Rapid Responder restriction (only specific hierarchy codes)
    rr_allowed_codes = {"01", "06", "05", "02", "04"}
    rr_allowed_ids = {e['id'] for e in employees if e['hierarchy_code'] in rr_allowed_codes}
    if "R" in shift_codes:
        for e in employees:
            if e['id'] not in rr_allowed_ids:
                for d in days_range:
                    model.Add(X[(e['id'], d, "R")] == 0)

    # C12: Sunday rules — max consecutive worked Sundays (config-driven)
    non_working = {"REPOS", "VA", "C", "E", "CMHN", "COLL", "FO9"}
    sundays = [d for d in days_range if get_date(d).weekday() == 6]
    worked_sun = {}
    for e in emp_ids:
        for sun in sundays:
            worked_sun[(e, sun)] = model.NewBoolVar(f"ws_{e}_{sun}")
            active = [X[(e, sun, s)] for s in shift_codes if s not in non_working]
            if sun - 1 in days_range:
                active.append(X[(e, sun - 1, "AMNP")])
            model.AddMaxEquality(worked_sun[(e, sun)], active)

    window_size = max_consecutive_sundays
    for e in emp_ids:
        for i in range(len(sundays) - window_size + 1):
            window_suns = sundays[i:i + window_size]
            model.Add(sum(worked_sun[(e, s)] for s in window_suns) <= window_size - 1)

    # C13: CMHN recovery
    for e_obj in employees:
        eid = e_obj['id']
        if e_obj['activity_type'] in ('cs', 'sec'):
            continue  # cs/sec don't do night shifts → no CMHN needed
        if "CMHN" not in shift_codes or "AMNP" not in shift_codes:
            continue
        total_nights = sum(X[(eid, d, "AMNP")] for d in days_range)
        has_cmhn = sum(X[(eid, d, "CMHN")] for d in days_range)

        is_heavy = model.NewBoolVar(f"heavy_{eid}")
        model.Add(total_nights >= 3).OnlyEnforceIf(is_heavy)
        model.Add(total_nights < 3).OnlyEnforceIf(is_heavy.Not())

        base_mins = int(e_obj.get('cmhn_starting_balance') or 0)
        needs_cmhn = model.NewBoolVar(f"ncmhn_{eid}")
        model.Add(base_mins > 480).OnlyEnforceIf(needs_cmhn)
        model.Add(base_mins <= 480).OnlyEnforceIf(needs_cmhn.Not())

        cond = model.NewBoolVar(f"cc_{eid}")
        model.AddMaxEquality(cond, [is_heavy, needs_cmhn])
        model.Add(has_cmhn >= 1).OnlyEnforceIf(cond)

    # ═══ SOFT CONSTRAINTS (penalties) ═══
    penalties = []

    # P1: Penalize isolated working days
    working_day = {}
    for e in emp_ids:
        for d in days_range:
            working_day[(e, d)] = model.NewBoolVar(f"wd_{e}_{d}")
            model.AddMaxEquality(
                working_day[(e, d)],
                [X[(e, d, s)] for s in shift_codes if s not in ("REPOS", "VA", "C", "CMHN")]
            )

    for e in emp_ids:
        for d in range(2, num_days):
            iso = model.NewBoolVar(f"iso_{e}_{d}")
            model.AddImplication(iso, working_day[(e, d)])
            model.AddImplication(iso, working_day[(e, d - 1)].Not())
            model.AddImplication(iso, working_day[(e, d + 1)].Not())
            penalties.append(iso * 100)

    # ─── Equity weights from config ──────────────────────────
    cfg_equite = cfg.get("equite", {})
    eq_weights = cfg_equite.get("weights", {})
    w_gardes = int(eq_weights.get("gardes", 1))
    w_nuits = int(eq_weights.get("nuits", 1))
    w_weekends = int(eq_weights.get("weekends", 1))
    w_heures = int(eq_weights.get("heures", 1))

    # P2: Equity — penalize deviation from FTE-proportional shift distribution
    # Calculate target operational shifts per employee based on FTE
    op_shifts = [s for s in ["AMJP", "AMNP", "AMHS"] if s in shift_codes]
    if op_shifts and w_gardes > 0:
        total_fte = sum(emp_by_id[e].get('fte_percent', 100) or 100 for e in emp_ids
                        if emp_by_id[e]['activity_type'] not in ('cs', 'sec'))

        # ─── P3: Historical bias (multi-month equity) ──────────
        history_months = cfg_equite.get("history_months", 3)
        hist_deltas: Dict[str, int] = {}

        if history_months > 0:
            hist_end = start_date - timedelta(days=1)
            hist_start = start_date - relativedelta(months=history_months)
            hist_num_days = (hist_end - hist_start).days + 1

            try:
                hist_res = supabase.table("schedule_entries")\
                    .select("personnel_id, shift_code")\
                    .gte("entry_date", hist_start.isoformat())\
                    .lte("entry_date", hist_end.isoformat())\
                    .in_("shift_code", op_shifts)\
                    .execute()
                hist_rows = hist_res.data or []
            except Exception:
                hist_rows = []

            # Count historical operational shifts per employee
            hist_counts: Dict[str, int] = {}
            for row in hist_rows:
                pid = row["personnel_id"]
                hist_counts[pid] = hist_counts.get(pid, 0) + 1

            # Compute FTE-proportional targets over the historical window
            total_ops_per_day_hist = sum(weekday_needs.get(s, 0) for s in op_shifts)
            for e in emp_ids:
                emp = emp_by_id[e]
                if emp['activity_type'] in ('cs', 'sec'):
                    continue
                fte = emp.get('fte_percent', 100) or 100
                fte_ratio = fte / max(total_fte, 1)
                hist_target = int(total_ops_per_day_hist * hist_num_days * fte_ratio)
                hist_actual = hist_counts.get(e, 0)
                # Positive = overworked historically → should get fewer shifts
                hist_deltas[e] = hist_actual - hist_target

        # ─── Apply P2 + P3 per employee (scaled by w_gardes) ───
        for e in emp_ids:
            emp = emp_by_id[e]
            if emp['activity_type'] in ('cs', 'sec'):
                continue
            fte = emp.get('fte_percent', 100) or 100
            # Expected proportion of operational shifts
            fte_ratio = fte / max(total_fte, 1)
            # Approximate target: total coverage * ratio
            total_ops_per_day = sum(weekday_needs.get(s, 0) for s in op_shifts)
            base_target = int(total_ops_per_day * num_days * fte_ratio)

            # Adjust target by historical delta (positive hist_delta → fewer shifts)
            hist_adj = hist_deltas.get(e, 0)
            target_ops = max(0, base_target - hist_adj)

            actual_ops = sum(X[(e, d, s)] for d in days_range for s in op_shifts)

            # P2: Penalize within-month deviation
            over = model.NewIntVar(0, num_days * 4, f"eq_over_{e}")
            under = model.NewIntVar(0, num_days * 4, f"eq_under_{e}")
            model.Add(actual_ops - target_ops == over - under)
            penalties.append(over * 10 * w_gardes)
            penalties.append(under * 10 * w_gardes)

            # P3: Additional gentle penalty for historical imbalance magnitude
            if hist_adj != 0:
                penalties.append(over * 5 * w_gardes)
                penalties.append(under * 5 * w_gardes)

    # ─── P2b: Night equity — equalize AMNP across employees ─
    if "AMNP" in shift_codes and w_nuits > 0:
        nuit_total_fte = sum(emp_by_id[e].get('fte_percent', 100) or 100 for e in emp_ids
                            if emp_by_id[e]['activity_type'] not in ('cs', 'sec'))
        for e in emp_ids:
            emp = emp_by_id[e]
            if emp['activity_type'] in ('cs', 'sec'):
                continue
            fte = emp.get('fte_percent', 100) or 100
            fte_ratio = fte / max(nuit_total_fte, 1)
            target_nights = int(sum(1 for d in days_range if get_date(d).weekday() < 5) * fte_ratio)
            actual_nights = sum(X[(e, d, "AMNP")] for d in days_range)
            n_over = model.NewIntVar(0, num_days, f"nuit_over_{e}")
            n_under = model.NewIntVar(0, num_days, f"nuit_under_{e}")
            model.Add(actual_nights - target_nights == n_over - n_under)
            penalties.append(n_over * 8 * w_nuits)
            penalties.append(n_under * 8 * w_nuits)

    # ─── P2c: Weekend equity — equalize Sunday work across employees ─
    sundays_list = [d for d in days_range if get_date(d).weekday() == 6]
    if sundays_list and w_weekends > 0:
        we_total_fte = sum(emp_by_id[e].get('fte_percent', 100) or 100 for e in emp_ids
                          if emp_by_id[e]['activity_type'] not in ('cs', 'sec'))
        working_shifts = [s for s in shift_codes if s not in ("REPOS", "VA", "C", "CMHN")]
        for e in emp_ids:
            emp = emp_by_id[e]
            if emp['activity_type'] in ('cs', 'sec'):
                continue
            fte = emp.get('fte_percent', 100) or 100
            fte_ratio = fte / max(we_total_fte, 1)
            target_sundays = max(1, int(len(sundays_list) * fte_ratio))
            actual_sundays = sum(
                X[(e, d, s)] for d in sundays_list for s in working_shifts
            )
            s_over = model.NewIntVar(0, len(sundays_list), f"we_over_{e}")
            s_under = model.NewIntVar(0, len(sundays_list), f"we_under_{e}")
            model.Add(actual_sundays - target_sundays == s_over - s_under)
            penalties.append(s_over * 8 * w_weekends)
            penalties.append(s_under * 8 * w_weekends)

    # ─── P2d: Hours equity — equalize total working shifts per FTE ─
    rest_codes = {"REPOS", "VA", "C", "CMHN", "M", "F", "I", "DI", "ACC"}
    working_codes = [s for s in shift_codes if s not in rest_codes]
    if working_codes and w_heures > 0:
        h_total_fte = sum(emp_by_id[e].get('fte_percent', 100) or 100 for e in emp_ids
                         if emp_by_id[e]['activity_type'] not in ('cs', 'sec'))
        for e in emp_ids:
            emp = emp_by_id[e]
            if emp['activity_type'] in ('cs', 'sec'):
                continue
            fte = emp.get('fte_percent', 100) or 100
            fte_ratio = fte / max(h_total_fte, 1)
            target_working = int(num_days * fte_ratio * len(working_codes) /
                                 max(len(shift_codes), 1) * len(emp_ids))
            actual_working = sum(X[(e, d, s)] for d in days_range for s in working_codes)
            h_over = model.NewIntVar(0, num_days * len(working_codes), f"h_over_{e}")
            h_under = model.NewIntVar(0, num_days * len(working_codes), f"h_under_{e}")
            model.Add(actual_working - target_working == h_over - h_under)
            penalties.append(h_over * 6 * w_heures)
            penalties.append(h_under * 6 * w_heures)

    # ─── P4: Employee preference penalties ─────────────────
    for pref in all_prefs:
        eid = pref["personnel_id"]
        if eid not in emp_by_id:
            continue
        ptype = pref["pref_type"]
        scode = pref.get("shift_code")

        if ptype == "avoid_shift" and scode and scode in shift_codes:
            for d in pref_matching_days(pref):
                if (eid, d) not in manual_map:
                    penalties.append(X[(eid, d, scode)] * 3)

        elif ptype == "prefer_shift" and scode and scode in shift_codes:
            for d in pref_matching_days(pref):
                if (eid, d) not in manual_map:
                    penalties.append(X[(eid, d, scode)] * -2)  # reward

        elif ptype == "prefer_day_off":
            for d in pref_matching_days(pref):
                if (eid, d) not in manual_map:
                    # Penalize NOT being on repos
                    not_repos = X[(eid, d, "REPOS")].Not()
                    penalties.append(not_repos * 5)

    model.Minimize(sum(penalties))

    # ─── Solve ─────────────────────────────────────────────
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 120.0
    solver.parameters.num_search_workers = 8

    status = solver.Solve(model)

    if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        # Persist results
        solver_payload = []
        for e in emp_ids:
            for d in days_range:
                if (e, d) in manual_map:
                    continue
                for s in shift_codes:
                    if s == "REPOS":
                        continue
                    if solver.BooleanValue(X[(e, d, s)]):
                        solver_payload.append({
                            "personnel_id": e,
                            "entry_date": get_date(d).isoformat(),
                            "shift_code": s,
                            "source": "solver",
                            "is_locked": False,
                        })

        # Delete old solver entries
        supabase.table("schedule_entries")\
            .delete()\
            .gte("entry_date", start_date.isoformat())\
            .lte("entry_date", end_date.isoformat())\
            .eq("source", "solver")\
            .execute()

        # Insert new in batches
        for i in range(0, len(solver_payload), 500):
            batch = solver_payload[i:i + 500]
            supabase.table("schedule_entries").upsert(
                batch, on_conflict="personnel_id,entry_date"
            ).execute()

        # Build response
        schedule = []
        for d in days_range:
            day_sched = {"date": get_date(d).isoformat(), "shifts": {}}
            for s in ["AMJP", "AMNP", "AMHS", "R", "CMHN"]:
                staff = [emp_by_id[e] for e in emp_ids if solver.BooleanValue(X[(e, d, s)])]
                day_sched["shifts"][s] = [
                    {"name": f"{st['last_name']} {st['first_name']}", "id": st['id']} for st in staff
                ]
            schedule.append(day_sched)

        return {
            "status": "success",
            "solver_status": solver.StatusName(status),
            "penalty": solver.ObjectiveValue(),
            "entries_saved": len(solver_payload),
            "schedule": schedule,
        }
    else:
        return {
            "status": "error",
            "message": f"Modèle infaisable ({solver.StatusName(status)}). Vérifiez les contraintes manuelles/importées.",
        }


# ═══════════════════════════════════════════════════════════
# EXPLAIN endpoint
# ═══════════════════════════════════════════════════════════

@app.post("/explain", dependencies=[Depends(verify_api_key)])
def explain_solver(request: SolverRequest):
    """Run solver in diagnostic mode — no DB write.
    Returns constraint-level diagnostics explaining why a month
    is infeasible or which constraints are tight.
    """
    try:
        result = _do_explain(request.year, request.month)
        return result
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


def _do_explain(target_year: int, target_month: int) -> dict:
    """Explain mode: run full solve (read-only) then report diagnostics."""

    # ─── Re-use same data loading as _do_solve ────────────
    cfg = load_config()
    cfg_heures = cfg["heures"]
    cfg_nuits = cfg["nuits"]
    cfg_weekends = cfg["weekends"]
    cfg_gardes = cfg["gardes"]

    max_week_minutes = int(cfg_heures["max_week"] * 60)
    max_month_overshoot_pct = cfg_heures.get("max_month_overshoot_pct", 10)
    max_consecutive_nights = cfg_nuits.get("max_consecutive", 3)
    max_consecutive_sundays = cfg_weekends.get("max_consecutive_sundays", 3)
    weekday_needs = cfg_gardes.get("weekday", {"AMJP": 4, "AMNP": 4, "AMHS": 2, "R": 2})
    saturday_needs = cfg_gardes.get("saturday", {"AMJP": 4, "AMNP": 4, "AMHS": 2, "R": 0})
    sunday_needs = cfg_gardes.get("weekend", {"AMJP": 4, "AMNP": 4, "AMHS": 0, "R": 0})

    start_date = date(target_year, target_month, 1)
    end_date = start_date + relativedelta(months=1, days=-1)
    num_days = (end_date - start_date).days + 1
    days_range = range(1, num_days + 1)

    def get_date(day_int):
        return date(target_year, target_month, day_int)

    personnel_res = supabase.table("personnel").select("*").eq("is_active", True).execute()
    shifts_res = supabase.table("shift_types").select("*").execute()
    employees = personnel_res.data
    shifts = shifts_res.data

    shift_by_code = {s['code']: s for s in shifts}
    shift_codes = list(shift_by_code.keys()) + ["REPOS"]
    emp_ids = [e['id'] for e in employees]
    emp_by_id = {e['id']: e for e in employees}

    manual_entries_res = supabase.table("schedule_entries")\
        .select("*")\
        .gte("entry_date", start_date.isoformat())\
        .lte("entry_date", end_date.isoformat())\
        .in_("source", ["manual", "import"])\
        .execute()

    manual_entries = manual_entries_res.data
    manual_map = {}
    for entry in manual_entries:
        d = date.fromisoformat(entry['entry_date']).day
        manual_map[(entry['personnel_id'], d)] = entry['shift_code']

    last_day_prev = start_date - timedelta(days=1)
    try:
        history_res = supabase.table("schedule_entries")\
            .select("personnel_id, shift_code")\
            .eq("entry_date", last_day_prev.isoformat())\
            .execute()
        history_map = {row['personnel_id']: row['shift_code'] for row in history_res.data}
    except Exception:
        history_map = {}

    # ─── Identify absences locked ──────────────────────────
    absence_codes = {"VA", "M", "ANP", "AP", "E", "FO9", "C", "CMHN", "QC1", "FIN",
                     "MAR", "CMAT", "CPAT", "SM", "DEC", "CNP", "COLL"}
    vaud_holidays = get_vaud_holidays(target_year)

    # ─── Quick infeasibility probe ─────────────────────────
    # First, run full model just like _do_solve but with a short time limit
    from ortools.sat.python import cp_model as _cp

    model = _cp.CpModel()
    X = {}
    for e in emp_ids:
        for d in days_range:
            for s in shift_codes:
                X[(e, d, s)] = model.NewBoolVar(f"x_{e}_{d}_{s}")

    # Apply all constraints exactly like _do_solve
    # C1
    for e in emp_ids:
        for d in days_range:
            model.AddExactlyOne([X[(e, d, s)] for s in shift_codes])
    # C2
    for (e, d), s_code in manual_map.items():
        if s_code in shift_codes:
            model.Add(X[(e, d, s_code)] == 1)
    # C3
    incompatible_after_night = ["AMJP", "AMHS", "A1", "A2", "6FM", "6P1"]
    for e in emp_ids:
        if history_map.get(e) == "AMNP":
            for next_s in incompatible_after_night:
                if next_s in shift_codes:
                    model.Add(X[(e, 1, next_s)] == 0)
        for d in range(1, num_days):
            for next_s in incompatible_after_night:
                if next_s in shift_codes:
                    model.AddImplication(X[(e, d, "AMNP")], X[(e, d + 1, next_s)].Not())
    # C4
    if "AMNP" in shift_codes:
        window = max_consecutive_nights + 1
        for e in emp_ids:
            for d_start in range(1, num_days - window + 2):
                d_end = min(d_start + window - 1, num_days)
                days_window = list(range(d_start, d_end + 1))
                if len(days_window) > max_consecutive_nights:
                    model.Add(sum(X[(e, d, "AMNP")] for d in days_window) <= max_consecutive_nights)
    # C5
    operational_shifts = ["AMJP", "AMNP", "AMHS", "R", "RS", "AMBCE"]
    for e in employees:
        eid = e['id']
        act_type = e['activity_type']
        h_code = e['hierarchy_code']
        if act_type in ("cs", "sec"):
            for d in days_range:
                if (eid, d) in manual_map:
                    continue
                for s in operational_shifts:
                    if s in shift_codes:
                        model.Add(X[(eid, d, s)] == 0)
                if get_date(d).weekday() >= 5:
                    for s in shift_codes:
                        if s != "REPOS":
                            model.Add(X[(eid, d, s)] == 0)
        if h_code == "01":
            for d in days_range:
                if (eid, d) in manual_map:
                    continue
                for s in ["AMNP", "AMJP", "AMHS"]:
                    if s in shift_codes:
                        model.Add(X[(eid, d, s)] == 0)
                if get_date(d).weekday() >= 5:
                    for s in shift_codes:
                        if s != "REPOS":
                            model.Add(X[(eid, d, s)] == 0)
                elif get_date(d).weekday() in (0, 1):
                    admin_shifts = [s for s in ["A1", "A2"] if s in shift_codes]
                    if admin_shifts:
                        model.Add(sum(X[(eid, d, s)] for s in admin_shifts) == 1)
    # C6
    for e_student in employees:
        if e_student.get('reference_rescuer_id'):
            ref_id = e_student['reference_rescuer_id']
            eid_student = e_student['id']
            if ref_id in emp_ids:
                for d in days_range:
                    for s in ["AMJP", "AMNP", "AMHS", "R"]:
                        if s in shift_codes:
                            model.Add(X[(eid_student, d, s)] == X[(ref_id, d, s)])
    # C7
    weeks = defaultdict(list)
    for d in days_range:
        iso = get_date(d).isocalendar()
        weeks[(iso[0], iso[1])].append(d)
    for week_key, days_in_week in weeks.items():
        for e in emp_ids:
            fte = emp_by_id[e].get('fte_percent') or 100
            limit = int(max_week_minutes * fte / 100)
            week_min = []
            for d in days_in_week:
                for s in shift_codes:
                    if s != "REPOS" and s in shift_by_code:
                        dur = shift_by_code[s].get("gross_minutes") or 0
                        if dur > 0:
                            week_min.append(X[(e, d, s)] * dur)
            if week_min:
                model.Add(sum(week_min) <= limit)
    # C8
    for e in employees:
        eid = e['id']
        fte = e.get('fte_percent', 100) or 100
        monthly_contract_min = 10400 * (fte / 100.0)
        monthly_limit = int(monthly_contract_min * (1 + max_month_overshoot_pct / 100))
        month_min = []
        for d in days_range:
            for s in shift_codes:
                if s != "REPOS" and s in shift_by_code:
                    dur = shift_by_code[s].get("gross_minutes") or 0
                    if dur > 0:
                        month_min.append(X[(eid, d, s)] * dur)
        if month_min:
            model.Add(sum(month_min) <= monthly_limit)
    # C9
    for d in days_range:
        dd = get_date(d)
        is_saturday = dd.weekday() == 5
        is_sunday = dd.weekday() == 6
        is_holiday = dd in vaud_holidays
        if is_holiday or is_sunday:
            needs = sunday_needs
        elif is_saturday:
            needs = saturday_needs
        else:
            needs = weekday_needs
        locked_out = sum(1 for e in emp_ids if manual_map.get((e, d)) in absence_codes)
        available_pool = len(emp_ids) - locked_out
        for s_code, required in needs.items():
            if s_code not in shift_codes or required == 0:
                continue
            cap = min(required, max(0, available_pool - required))
            model.Add(sum(X[(e, d, s_code)] for e in emp_ids) >= cap)
            model.Add(sum(X[(e, d, s_code)] for e in emp_ids) <= required)
    # C10
    sd_emp_ids = [e['id'] for e in employees if e['activity_type'] == 'ta']
    for d in days_range:
        for s, mx in [("AMJP", 2), ("AMNP", 2), ("AMHS", 1)]:
            if s in shift_codes:
                model.Add(sum(X[(eid, d, s)] for eid in sd_emp_ids) <= mx)
    # C11
    rr_allowed_codes = {"01", "06", "05", "02", "04"}
    rr_allowed_ids = {e['id'] for e in employees if e['hierarchy_code'] in rr_allowed_codes}
    if "R" in shift_codes:
        for e in employees:
            if e['id'] not in rr_allowed_ids:
                for d in days_range:
                    model.Add(X[(e['id'], d, "R")] == 0)
    # C12
    non_working = {"REPOS", "VA", "C", "E", "CMHN", "COLL", "FO9"}
    sundays = [d for d in days_range if get_date(d).weekday() == 6]
    worked_sun = {}
    for e in emp_ids:
        for sun in sundays:
            worked_sun[(e, sun)] = model.NewBoolVar(f"ws_{e}_{sun}")
            active = [X[(e, sun, s)] for s in shift_codes if s not in non_working]
            if sun - 1 in days_range:
                active.append(X[(e, sun - 1, "AMNP")])
            model.AddMaxEquality(worked_sun[(e, sun)], active)
    window_size = max_consecutive_sundays
    for e in emp_ids:
        for i in range(len(sundays) - window_size + 1):
            window_suns = sundays[i:i + window_size]
            model.Add(sum(worked_sun[(e, s)] for s in window_suns) <= window_size - 1)
    # C13
    for e_obj in employees:
        eid = e_obj['id']
        if e_obj['activity_type'] in ('cs', 'sec'):
            continue
        if "CMHN" not in shift_codes or "AMNP" not in shift_codes:
            continue
        total_nights = sum(X[(eid, d, "AMNP")] for d in days_range)
        has_cmhn = sum(X[(eid, d, "CMHN")] for d in days_range)
        is_heavy = model.NewBoolVar(f"heavy_{eid}")
        model.Add(total_nights >= 3).OnlyEnforceIf(is_heavy)
        model.Add(total_nights < 3).OnlyEnforceIf(is_heavy.Not())
        base_mins = int(e_obj.get('cmhn_starting_balance') or 0)
        needs_cmhn = model.NewBoolVar(f"ncmhn_{eid}")
        model.Add(base_mins > 480).OnlyEnforceIf(needs_cmhn)
        model.Add(base_mins <= 480).OnlyEnforceIf(needs_cmhn.Not())
        cond = model.NewBoolVar(f"cc_{eid}")
        model.AddMaxEquality(cond, [is_heavy, needs_cmhn])
        model.Add(has_cmhn >= 1).OnlyEnforceIf(cond)

    # Soft constraints same as main solver
    penalties = []
    working_day = {}
    for e in emp_ids:
        for d in days_range:
            working_day[(e, d)] = model.NewBoolVar(f"wd_{e}_{d}")
            model.AddMaxEquality(
                working_day[(e, d)],
                [X[(e, d, s)] for s in shift_codes if s not in ("REPOS", "VA", "C", "CMHN")]
            )
    iso_penalties = []
    for e in emp_ids:
        for d in range(2, num_days):
            iso_var = model.NewBoolVar(f"iso_{e}_{d}")
            model.AddImplication(iso_var, working_day[(e, d)])
            model.AddImplication(iso_var, working_day[(e, d - 1)].Not())
            model.AddImplication(iso_var, working_day[(e, d + 1)].Not())
            iso_penalties.append(iso_var * 100)
            penalties.append(iso_var * 100)

    # ─── Equity weights from config ──────────────────────────
    cfg_equite = cfg.get("equite", {})
    eq_weights = cfg_equite.get("weights", {})
    w_gardes = int(eq_weights.get("gardes", 1))
    w_nuits = int(eq_weights.get("nuits", 1))
    w_weekends = int(eq_weights.get("weekends", 1))

    op_shifts = [s for s in ["AMJP", "AMNP", "AMHS"] if s in shift_codes]
    equity_penalties = []
    if op_shifts and w_gardes > 0:
        total_fte = sum(emp_by_id[e].get('fte_percent', 100) or 100 for e in emp_ids
                        if emp_by_id[e]['activity_type'] not in ('cs', 'sec'))
        for e in emp_ids:
            emp = emp_by_id[e]
            if emp['activity_type'] in ('cs', 'sec'):
                continue
            fte = emp.get('fte_percent', 100) or 100
            fte_ratio = fte / max(total_fte, 1)
            total_ops_per_day = sum(weekday_needs.get(s, 0) for s in op_shifts)
            target_ops = int(total_ops_per_day * num_days * fte_ratio)
            actual_ops = sum(X[(e, d, s)] for d in days_range for s in op_shifts)
            over = model.NewIntVar(0, num_days * 4, f"eq_over_{e}")
            under = model.NewIntVar(0, num_days * 4, f"eq_under_{e}")
            model.Add(actual_ops - target_ops == over - under)
            equity_penalties.append(over * 10 * w_gardes)
            equity_penalties.append(under * 10 * w_gardes)
            penalties.append(over * 10 * w_gardes)
            penalties.append(under * 10 * w_gardes)

    # P2b: Night equity (probe solver)
    if "AMNP" in shift_codes and w_nuits > 0:
        nuit_total_fte = sum(emp_by_id[e].get('fte_percent', 100) or 100 for e in emp_ids
                            if emp_by_id[e]['activity_type'] not in ('cs', 'sec'))
        for e in emp_ids:
            emp = emp_by_id[e]
            if emp['activity_type'] in ('cs', 'sec'):
                continue
            fte = emp.get('fte_percent', 100) or 100
            fte_ratio = fte / max(nuit_total_fte, 1)
            target_nights = int(sum(1 for d in days_range if get_date(d).weekday() < 5) * fte_ratio)
            actual_nights = sum(X[(e, d, "AMNP")] for d in days_range)
            n_over = model.NewIntVar(0, num_days, f"nuit_over_{e}")
            n_under = model.NewIntVar(0, num_days, f"nuit_under_{e}")
            model.Add(actual_nights - target_nights == n_over - n_under)
            penalties.append(n_over * 8 * w_nuits)
            penalties.append(n_under * 8 * w_nuits)

    # P2c: Weekend equity (probe solver)
    sundays_list = [d for d in days_range if get_date(d).weekday() == 6]
    if sundays_list and w_weekends > 0:
        we_total_fte = sum(emp_by_id[e].get('fte_percent', 100) or 100 for e in emp_ids
                          if emp_by_id[e]['activity_type'] not in ('cs', 'sec'))
        working_shifts = [s for s in shift_codes if s not in ("REPOS", "VA", "C", "CMHN")]
        for e in emp_ids:
            emp = emp_by_id[e]
            if emp['activity_type'] in ('cs', 'sec'):
                continue
            fte = emp.get('fte_percent', 100) or 100
            fte_ratio = fte / max(we_total_fte, 1)
            target_sundays = max(1, int(len(sundays_list) * fte_ratio))
            actual_sundays = sum(
                X[(e, d, s)] for d in sundays_list for s in working_shifts
            )
            s_over = model.NewIntVar(0, len(sundays_list), f"we_over_{e}")
            s_under = model.NewIntVar(0, len(sundays_list), f"we_under_{e}")
            model.Add(actual_sundays - target_sundays == s_over - s_under)
            penalties.append(s_over * 8 * w_weekends)
            penalties.append(s_under * 8 * w_weekends)

    model.Minimize(sum(penalties))

    # ─── Solve with short time limit ───────────────────────
    solver = _cp.CpSolver()
    solver.parameters.max_time_in_seconds = 60.0
    solver.parameters.num_search_workers = 8
    status = solver.Solve(model)

    diagnostics = []

    if status in (_cp.OPTIMAL, _cp.FEASIBLE):
        total_penalty = solver.ObjectiveValue()
        iso_cost = sum(solver.Value(v) for v in iso_penalties) if iso_penalties else 0
        eq_cost = sum(solver.Value(v) for v in equity_penalties) if equity_penalties else 0

        diagnostics.append({
            "constraint": "Pénalité totale",
            "status": "ok",
            "detail": f"Coût objectif = {total_penalty:.0f} (isolation: {iso_cost:.0f}, équité: {eq_cost:.0f})"
        })

        # Weekly hours headroom
        tight_weeks = []
        for week_key, days_in_week in weeks.items():
            for e in emp_ids:
                fte = emp_by_id[e].get('fte_percent') or 100
                limit = int(max_week_minutes * fte / 100)
                actual = sum(
                    (shift_by_code[s].get("gross_minutes") or 0) * solver.Value(X[(e, d, s)])
                    for d in days_in_week for s in shift_codes
                    if s != "REPOS" and s in shift_by_code
                )
                headroom = limit - actual
                if headroom <= 60:  # ≤ 1h headroom
                    name = emp_by_id[e].get('last_name', e)
                    tight_weeks.append(f"{name} S{week_key[1]}: {actual//60}h/{limit//60}h")

        if tight_weeks:
            diagnostics.append({
                "constraint": "C7 — Limite 50h/sem",
                "status": "warning",
                "detail": f"{len(tight_weeks)} cas serrés: {', '.join(tight_weeks[:5])}"
            })

        # Monthly hours headroom
        tight_months = []
        for e in employees:
            eid = e['id']
            fte = e.get('fte_percent', 100) or 100
            monthly_contract_min = 10400 * (fte / 100.0)
            monthly_limit = int(monthly_contract_min * (1 + max_month_overshoot_pct / 100))
            actual = sum(
                (shift_by_code[s].get("gross_minutes") or 0) * solver.Value(X[(eid, d, s)])
                for d in days_range for s in shift_codes
                if s != "REPOS" and s in shift_by_code
            )
            headroom = monthly_limit - actual
            if headroom <= 120:
                tight_months.append(f"{e.get('last_name', eid)}: {actual//60}h/{monthly_limit//60}h")

        if tight_months:
            diagnostics.append({
                "constraint": "C8 — Limite mensuelle",
                "status": "warning",
                "detail": f"{len(tight_months)} cas serrés: {', '.join(tight_months[:5])}"
            })

        # Coverage gaps
        coverage_issues = []
        for d in days_range:
            dd = get_date(d)
            is_saturday = dd.weekday() == 5
            is_sunday = dd.weekday() == 6
            is_holiday = dd in vaud_holidays
            if is_holiday or is_sunday:
                needs = sunday_needs
            elif is_saturday:
                needs = saturday_needs
            else:
                needs = weekday_needs
            for s_code, required in needs.items():
                if s_code not in shift_codes or required == 0:
                    continue
                actual = sum(solver.Value(X[(e, d, s_code)]) for e in emp_ids)
                if actual < required:
                    coverage_issues.append(f"{dd.strftime('%d/%m')} {s_code}: {actual}/{required}")

        if coverage_issues:
            diagnostics.append({
                "constraint": "C9 — Couverture journalière",
                "status": "warning",
                "detail": f"{len(coverage_issues)} sous-couvertures: {', '.join(coverage_issues[:5])}"
            })
        else:
            diagnostics.append({
                "constraint": "C9 — Couverture journalière",
                "status": "ok",
                "detail": "Toutes les gardes sont couvertes."
            })

        # Night distribution
        night_counts = {}
        for e in emp_ids:
            nc = sum(solver.Value(X[(e, d, "AMNP")]) for d in days_range) if "AMNP" in shift_codes else 0
            if nc > 0:
                night_counts[emp_by_id[e].get('last_name', e)] = nc
        if night_counts:
            avg_nights = sum(night_counts.values()) / len(night_counts)
            max_name = max(night_counts, key=night_counts.get)
            diagnostics.append({
                "constraint": "Répartition des nuits",
                "status": "ok",
                "detail": f"Moyenne: {avg_nights:.1f}/pers, max: {max_name} ({night_counts[max_name]})"
            })

        return {
            "status": "success",
            "feasible": True,
            "penalty": total_penalty,
            "diagnostics": diagnostics,
        }

    else:
        # ─── INFEASIBLE — try dropping constraint groups ───
        diagnostics.append({
            "constraint": "Modèle",
            "status": "critical",
            "detail": f"Infaisable ({solver.StatusName(status)}). Analyse des contraintes…"
        })

        # Count absences per day
        per_day_absences = {}
        for d in days_range:
            abs_count = sum(1 for e in emp_ids if manual_map.get((e, d)) in absence_codes)
            if abs_count > 0:
                per_day_absences[get_date(d).strftime('%d/%m')] = abs_count

        if per_day_absences:
            worst_day = max(per_day_absences, key=per_day_absences.get)
            diagnostics.append({
                "constraint": "Absences bloquées",
                "status": "warning",
                "detail": f"{sum(per_day_absences.values())} absences manuelles. "
                          f"Pire jour: {worst_day} ({per_day_absences[worst_day]} absents sur {len(emp_ids)})"
            })

        # Check student pairing conflicts
        student_pairs = []
        for e_student in employees:
            if e_student.get('reference_rescuer_id'):
                ref_id = e_student['reference_rescuer_id']
                if ref_id in emp_ids:
                    # Check if reference has locked absences when student works
                    for d in days_range:
                        ref_locked = manual_map.get((ref_id, d))
                        stu_locked = manual_map.get((e_student['id'], d))
                        if ref_locked in absence_codes and stu_locked not in absence_codes and stu_locked is not None:
                            student_pairs.append(
                                f"{e_student.get('last_name')}/{emp_by_id[ref_id].get('last_name')} J{d}"
                            )

        if student_pairs:
            diagnostics.append({
                "constraint": "C6 — Paires étudiant-référent",
                "status": "critical",
                "detail": f"Conflits de verrouillage: {', '.join(student_pairs[:5])}"
            })

        # Check if coverage demands exceed available staff
        for d in days_range:
            dd = get_date(d)
            is_saturday = dd.weekday() == 5
            is_sunday = dd.weekday() == 6
            is_holiday = dd in vaud_holidays
            if is_holiday or is_sunday:
                needs = sunday_needs
            elif is_saturday:
                needs = saturday_needs
            else:
                needs = weekday_needs
            total_needed = sum(v for v in needs.values() if v > 0)
            abs_count = sum(1 for e in emp_ids if manual_map.get((e, d)) in absence_codes)
            cs_sec_count = sum(1 for e in employees
                               if e['activity_type'] in ('cs', 'sec'))
            available = len(emp_ids) - abs_count - cs_sec_count
            if available < total_needed:
                diagnostics.append({
                    "constraint": "C9 — Sous-effectif",
                    "status": "critical",
                    "detail": f"{dd.strftime('%d/%m')}: {total_needed} gardes requises "
                              f"mais seulement {available} dispo ({abs_count} absents, {cs_sec_count} cs/sec)"
                })

        return {
            "status": "success",
            "feasible": False,
            "diagnostics": diagnostics,
        }


# ═══════════════════════════════════════════════════════════
# PDF Upload endpoint
# ═══════════════════════════════════════════════════════════

@app.post("/upload-pdf", dependencies=[Depends(verify_api_key)])
async def upload_pdf(file: UploadFile = File(...), year: int = Form(...), month: int = Form(...)):
    if not file.filename.endswith('.pdf'):
        return {"status": "error", "message": "Only PDF files are supported"}

    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
        content = await file.read()
        tmp.write(content)
        tmp_path = tmp.name

    try:
        entries = extractor.process_single_pdf(tmp_path, year, month)
    except Exception as e:
        os.unlink(tmp_path)
        return {"status": "error", "message": f"Extraction failed: {str(e)}"}

    os.unlink(tmp_path)

    # Resolve hierarchy_code to personnel_id
    personnel_res = supabase.table("personnel").select("id, hierarchy_code").execute()
    personnel_map = {str(p["hierarchy_code"]): p["id"] for p in personnel_res.data}

    by_shift = defaultdict(int)
    by_employee = defaultdict(int)
    unknowns = []

    payload = []
    for e in entries:
        code = e["shift_code"]
        emp_code = str(e["hierarchy_code"])

        if code.startswith("UNK_"):
            if code not in unknowns:
                unknowns.append(code)
            continue

        by_shift[code] += 1
        by_employee[emp_code] += 1

        pid = personnel_map.get(emp_code)
        if pid:
            payload.append({
                "personnel_id": pid,
                "entry_date": e["date"],
                "shift_code": code,
                "is_locked": True,
                "source": "import",
            })

    # Purge old imports
    s_date = date(year, month, 1)
    e_date = s_date + relativedelta(months=1, days=-1)
    supabase.table("schedule_entries")\
        .delete()\
        .gte("entry_date", s_date.isoformat())\
        .lte("entry_date", e_date.isoformat())\
        .eq("source", "import")\
        .execute()

    if payload:
        supabase.table("schedule_entries").upsert(payload, on_conflict="personnel_id,entry_date").execute()

    entries_for_preview = [
        {"hierarchy_code": str(e["hierarchy_code"]), "date": e["date"], "shift_code": e["shift_code"]}
        for e in entries if not e["shift_code"].startswith("UNK_")
    ]

    # ── Validation report ────────────────────────────────────────────────
    days_in_month = (date(year, month % 12 + 1, 1) if month < 12 else date(year + 1, 1, 1)) - date(year, month, 1)
    num_days = days_in_month.days

    # Employees without any extracted entries
    all_active_codes = [
        code for code, info in extractor.EMPLOYEES.items()
        if info["type"] not in ("aux",) and info["fte"] > 0
    ]
    employees_without_entries = [c for c in all_active_codes if c not in by_employee]

    # Employees with excess entries (> days in month = likely parsing error)
    employees_with_excess = [
        {"code": emp, "count": cnt}
        for emp, cnt in by_employee.items()
        if cnt > num_days
    ]

    # Weekend coverage: count shifts on Saturdays and Sundays
    weekend_shifts = {"saturday": defaultdict(int), "sunday": defaultdict(int)}
    for e in entries_for_preview:
        d = date.fromisoformat(e["date"])
        if d.weekday() == 5:  # Saturday
            weekend_shifts["saturday"][e["shift_code"]] += 1
        elif d.weekday() == 6:  # Sunday
            weekend_shifts["sunday"][e["shift_code"]] += 1

    validation = {
        "days_in_month": num_days,
        "employees_expected": len(all_active_codes),
        "employees_found": len(by_employee),
        "employees_without_entries": employees_without_entries,
        "employees_with_excess_entries": employees_with_excess,
        "weekend_coverage": {
            "saturday": dict(weekend_shifts["saturday"]),
            "sunday": dict(weekend_shifts["sunday"]),
        },
    }

    return {
        "status": "success",
        "message": f"Extraction réussie : {len(payload)} entrées importées pour {len(by_employee)} collaborateurs",
        "count": len(payload),
        "summary": {
            "total_entries": len(payload),
            "by_shift": dict(by_shift),
            "by_employee": dict(by_employee),
            "unknowns": unknowns,
            "entries": entries_for_preview,
        },
        "validation": validation,
    }


# ── Known shift codes from extractor ─────────────────────────────────────────
KNOWN_SHIFT_CODES = set()
try:
    KNOWN_SHIFT_CODES = {c[3] for c in extractor.KNOWN_COLORS}
except Exception:
    KNOWN_SHIFT_CODES = {"AMNP", "AMJP", "AMHS", "R", "RS", "CMHN", "VA", "E", "FO9", "M", "ANP", "AP", "QC1", "C", "6FM", "6P1", "A2", "A1"}


def _extract_and_validate(tmp_path: str, year: int, month: int, filename: str):
    """Shared extraction + validation logic used by both validate and confirm flows."""
    entries = extractor.process_single_pdf(tmp_path, year, month)

    # Resolve hierarchy_code to personnel_id
    personnel_res = supabase.table("personnel").select("id, hierarchy_code").execute()
    personnel_map = {str(p["hierarchy_code"]): p["id"] for p in personnel_res.data}

    by_shift = defaultdict(int)
    by_employee = defaultdict(int)
    unknowns = []
    invalid_codes = []
    duplicates = []

    seen = set()  # (hierarchy_code, date) for duplicate detection
    payload = []
    for e in entries:
        code = e["shift_code"]
        emp_code = str(e["hierarchy_code"])
        entry_key = (emp_code, e["date"])

        if code.startswith("UNK_"):
            if code not in unknowns:
                unknowns.append(code)
            continue

        # Check for unknown shift codes (not in KNOWN_SHIFT_CODES)
        if code not in KNOWN_SHIFT_CODES:
            if code not in invalid_codes:
                invalid_codes.append(code)

        # Detect duplicates in extracted data
        if entry_key in seen:
            duplicates.append({"hierarchy_code": emp_code, "date": e["date"], "shift_code": code})
            continue
        seen.add(entry_key)

        by_shift[code] += 1
        by_employee[emp_code] += 1

        pid = personnel_map.get(emp_code)
        if pid:
            payload.append({
                "personnel_id": pid,
                "entry_date": e["date"],
                "shift_code": code,
                "is_locked": True,
                "source": "import",
            })

    entries_for_preview = [
        {"hierarchy_code": str(e["hierarchy_code"]), "date": e["date"], "shift_code": e["shift_code"]}
        for e in entries if not e["shift_code"].startswith("UNK_")
    ]

    # ── Validation report ────────────────────────────────────────────────
    days_in_month = (date(year, month % 12 + 1, 1) if month < 12 else date(year + 1, 1, 1)) - date(year, month, 1)
    num_days = days_in_month.days

    all_active_codes = [
        code for code, info in extractor.EMPLOYEES.items()
        if info["type"] not in ("aux",) and info["fte"] > 0
    ]
    employees_without_entries = [c for c in all_active_codes if c not in by_employee]

    employees_with_excess = [
        {"code": emp, "count": cnt}
        for emp, cnt in by_employee.items()
        if cnt > num_days
    ]

    # Date gap detection: employees with entries but missing > 30% of days
    date_gaps = []
    for emp, cnt in by_employee.items():
        expected = num_days
        if cnt < expected * 0.7 and cnt > 0:  # present but < 70%
            date_gaps.append({"code": emp, "entries": cnt, "expected": expected})

    # Check for conflicts with existing data
    s_date = date(year, month, 1)
    e_date = s_date + relativedelta(months=1, days=-1)
    existing_res = supabase.table("schedule_entries") \
        .select("personnel_id, entry_date, shift_code, source") \
        .gte("entry_date", s_date.isoformat()) \
        .lte("entry_date", e_date.isoformat()) \
        .execute()
    existing_count = len(existing_res.data) if existing_res.data else 0
    existing_import_count = sum(1 for r in (existing_res.data or []) if r.get("source") == "import")
    existing_manual_count = existing_count - existing_import_count

    weekend_shifts = {"saturday": defaultdict(int), "sunday": defaultdict(int)}
    for e in entries_for_preview:
        d = date.fromisoformat(e["date"])
        if d.weekday() == 5:
            weekend_shifts["saturday"][e["shift_code"]] += 1
        elif d.weekday() == 6:
            weekend_shifts["sunday"][e["shift_code"]] += 1

    validation = {
        "days_in_month": num_days,
        "employees_expected": len(all_active_codes),
        "employees_found": len(by_employee),
        "employees_without_entries": employees_without_entries,
        "employees_with_excess_entries": employees_with_excess,
        "duplicates_found": duplicates,
        "invalid_shift_codes": invalid_codes,
        "date_gaps": date_gaps,
        "existing_entries": {
            "total": existing_count,
            "import": existing_import_count,
            "manual": existing_manual_count,
        },
        "weekend_coverage": {
            "saturday": dict(weekend_shifts["saturday"]),
            "sunday": dict(weekend_shifts["sunday"]),
        },
    }

    summary = {
        "total_entries": len(payload),
        "by_shift": dict(by_shift),
        "by_employee": dict(by_employee),
        "unknowns": unknowns,
        "entries": entries_for_preview,
    }

    return payload, summary, validation, existing_res.data or []


@app.post("/upload-pdf-validate", dependencies=[Depends(verify_api_key)])
async def upload_pdf_validate(file: UploadFile = File(...), year: int = Form(...), month: int = Form(...)):
    """Step 1: Extract and validate only — no database writes."""
    if not file.filename.endswith('.pdf'):
        return {"status": "error", "message": "Only PDF files are supported"}

    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
        content = await file.read()
        tmp.write(content)
        tmp_path = tmp.name

    try:
        payload, summary, validation, _ = _extract_and_validate(tmp_path, year, month, file.filename)
    except Exception as e:
        os.unlink(tmp_path)
        return {"status": "error", "message": f"Extraction failed: {str(e)}"}

    os.unlink(tmp_path)

    # Generate a validation token and store payload in memory
    token = str(uuid_mod.uuid4())
    _pending_imports[token] = {
        "payload": payload,
        "summary": summary,
        "validation": validation,
        "filename": file.filename,
        "year": year,
        "month": month,
        "expires": time.time() + 3600,  # 1 hour TTL
    }

    # Clean up expired tokens
    now = time.time()
    expired = [k for k, v in _pending_imports.items() if v["expires"] < now]
    for k in expired:
        del _pending_imports[k]

    # Warnings for the UI
    warnings = []
    if validation["duplicates_found"]:
        warnings.append({"type": "duplicate", "severity": "warning",
                         "message": f"{len(validation['duplicates_found'])} entrée(s) dupliquée(s) détectée(s)",
                         "details": validation["duplicates_found"]})
    if validation["invalid_shift_codes"]:
        warnings.append({"type": "invalid_code", "severity": "warning",
                         "message": f"Code(s) de garde inconnu(s) : {', '.join(validation['invalid_shift_codes'])}",
                         "details": validation["invalid_shift_codes"]})
    if validation["date_gaps"]:
        warnings.append({"type": "date_gap", "severity": "info",
                         "message": f"{len(validation['date_gaps'])} collaborateur(s) avec des jours manquants",
                         "details": validation["date_gaps"]})
    if validation["existing_entries"]["manual"] > 0:
        warnings.append({"type": "overwrite", "severity": "info",
                         "message": f"{validation['existing_entries']['manual']} entrée(s) manuelle(s) existante(s) ne seront pas écrasées",
                         "details": validation["existing_entries"]})
    if validation["existing_entries"]["import"] > 0:
        warnings.append({"type": "reimport", "severity": "info",
                         "message": f"{validation['existing_entries']['import']} entrée(s) d'import précédent seront remplacées",
                         "details": validation["existing_entries"]})
    if summary["unknowns"]:
        warnings.append({"type": "unknown", "severity": "warning",
                         "message": f"{len(summary['unknowns'])} code(s) non reconnu(s) : {', '.join(summary['unknowns'])}"})

    return {
        "status": "validated",
        "token": token,
        "message": f"Extraction réussie : {len(payload)} entrées pour {len(summary['by_employee'])} collaborateurs — en attente de confirmation",
        "count": len(payload),
        "summary": summary,
        "validation": validation,
        "warnings": warnings,
    }


@app.post("/upload-pdf-confirm", dependencies=[Depends(verify_api_key)])
async def upload_pdf_confirm(token: str = Form(...), user_id: str = Form(None)):
    """Step 2: Commit validated data to Supabase and log import history."""
    pending = _pending_imports.pop(token, None)
    if not pending:
        return {"status": "error", "message": "Token invalide ou expiré. Veuillez relancer la validation."}

    if pending["expires"] < time.time():
        return {"status": "error", "message": "Token expiré. Veuillez relancer la validation."}

    payload = pending["payload"]
    year = pending["year"]
    month = pending["month"]
    filename = pending["filename"]

    # Purge old imports for this month
    s_date = date(year, month, 1)
    e_date = s_date + relativedelta(months=1, days=-1)
    supabase.table("schedule_entries") \
        .delete() \
        .gte("entry_date", s_date.isoformat()) \
        .lte("entry_date", e_date.isoformat()) \
        .eq("source", "import") \
        .execute()

    # Insert / upsert new entries and track new vs updated
    entries_new = 0
    entries_updated = 0
    entries_failed = 0

    if payload:
        try:
            # Check existing entries to classify as new vs updated
            existing_keys = set()
            existing_res = supabase.table("schedule_entries") \
                .select("personnel_id, entry_date") \
                .gte("entry_date", s_date.isoformat()) \
                .lte("entry_date", e_date.isoformat()) \
                .execute()
            for r in (existing_res.data or []):
                existing_keys.add((r["personnel_id"], r["entry_date"]))

            for p in payload:
                key = (p["personnel_id"], p["entry_date"])
                if key in existing_keys:
                    entries_updated += 1
                else:
                    entries_new += 1

            supabase.table("schedule_entries").upsert(payload, on_conflict="personnel_id,entry_date").execute()
        except Exception as e:
            entries_failed = len(payload)
            entries_new = 0
            entries_updated = 0

    # Determine status
    unknowns = pending["summary"].get("unknowns", [])
    if entries_failed > 0:
        status = "error"
    elif len(unknowns) > 0:
        status = "partial"
    else:
        status = "success"

    # Log import history
    history_entry = {
        "filename": filename,
        "year": year,
        "month": month,
        "entries_new": entries_new,
        "entries_updated": entries_updated,
        "entries_skipped": 0,
        "entries_failed": entries_failed,
        "unknowns": unknowns,
        "status": status,
    }
    if user_id:
        history_entry["user_id"] = user_id
    try:
        supabase.table("import_history").insert(history_entry).execute()
    except Exception:
        pass  # Don't fail the import if logging fails

    return {
        "status": status,
        "message": f"Import terminé : {entries_new} nouvelles, {entries_updated} mises à jour, {entries_failed} échouées",
        "report": {
            "entries_new": entries_new,
            "entries_updated": entries_updated,
            "entries_skipped": 0,
            "entries_failed": entries_failed,
            "unknowns": unknowns,
        },
        "summary": pending["summary"],
        "validation": pending["validation"],
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
