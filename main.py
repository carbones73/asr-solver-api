from fastapi import FastAPI, UploadFile, File, Form
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

# We assume extractor.py is alongside this file
try:
    import extractor
except ImportError:
    pass

app = FastAPI(title="ASR Solver API")

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
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


# ─── Config Loader ─────────────────────────────────────────
def load_config() -> Dict[str, Any]:
    """Load app_config from Supabase, fallback to defaults."""
    defaults = {
        "heures": {"max_week": 50, "max_month_overshoot_pct": 10, "dressing_minutes": 10},
        "nuits": {"max_consecutive": 3, "rest_after_night": True, "cmhn_rate_pct": 15},
        "weekends": {"max_consecutive_sundays": 3, "cs_sec_exempt": True},
        "gardes": {
            "weekday": {"AMJP": 4, "AMNP": 4, "AMHS": 2, "R": 2},
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


@app.get("/config")
def get_config():
    """Return current app_config values."""
    config = load_config()
    return {"status": "ok", "config": config}


@app.get("/status")
def get_status():
    """Return last solver run status."""
    return _last_run


@app.post("/clear")
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


@app.post("/solve")
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
    weekend_needs = cfg_gardes.get("weekend", {"AMJP": 4, "AMNP": 4, "AMHS": 0, "R": 0})

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
        is_weekend = dd.weekday() >= 5
        is_holiday = dd in vaud_holidays

        needs = weekend_needs if (is_weekend or is_holiday) else weekday_needs

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

    # P2: Equity — penalize deviation from FTE-proportional shift distribution
    # Calculate target operational shifts per employee based on FTE
    op_shifts = [s for s in ["AMJP", "AMNP", "AMHS"] if s in shift_codes]
    if op_shifts:
        total_fte = sum(emp_by_id[e].get('fte_percent', 100) or 100 for e in emp_ids
                        if emp_by_id[e]['activity_type'] not in ('cs', 'sec'))
        for e in emp_ids:
            emp = emp_by_id[e]
            if emp['activity_type'] in ('cs', 'sec'):
                continue
            fte = emp.get('fte_percent', 100) or 100
            # Expected proportion of operational shifts
            fte_ratio = fte / max(total_fte, 1)
            # Approximate target: total coverage * ratio
            total_ops_per_day = sum(weekday_needs.get(s, 0) for s in op_shifts)
            target_ops = int(total_ops_per_day * num_days * fte_ratio)

            actual_ops = sum(X[(e, d, s)] for d in days_range for s in op_shifts)

            # Penalize over/under with auxiliary variables
            over = model.NewIntVar(0, num_days * 4, f"eq_over_{e}")
            under = model.NewIntVar(0, num_days * 4, f"eq_under_{e}")
            model.Add(actual_ops - target_ops == over - under)
            penalties.append(over * 10)
            penalties.append(under * 10)

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
# PDF Upload endpoint
# ═══════════════════════════════════════════════════════════

@app.post("/upload-pdf")
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
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
