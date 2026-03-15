#!/usr/bin/env python3
"""Batch-upload all monthly PDFs to Supabase via pixel extraction.

Usage: python batch_upload.py [--mode pixel|ocr|auto]

Processes each PDF (janvier–juin 2026) one at a time, calling the same
extraction + Supabase upsert logic that the /upload-pdf endpoint uses.
"""
import os, sys, time
from datetime import date
from dateutil.relativedelta import relativedelta
from collections import defaultdict

# Ensure solver-api dir is on the path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ambulance_adapter

from supabase import create_client

# ── Config (read .env manually) ─────────────────────────────────────────
def _load_env():
    """Read .env from solver-api directory."""
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())

_load_env()

SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://dcijgpmpysyfcjeerxqn.supabase.co")
# Prefer service_role key for batch operations (bypasses RLS)
SUPABASE_KEY = (
    os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    or os.environ.get("SUPABASE_KEY")
    or ""
)

if not SUPABASE_KEY:
    print("ERROR: No SUPABASE_KEY or SUPABASE_SERVICE_ROLE_KEY found in .env or environment")
    sys.exit(1)

print(f"Using key prefix: {SUPABASE_KEY[:40]}...")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# ── PDF files ────────────────────────────────────────────────────────────
PDF_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "_archive")
MONTHS = [
    ("janvier2026.pdf",  2026, 1),
    ("fevrier2026.pdf",  2026, 2),
    ("mars2026.pdf",     2026, 3),
    ("avril2026.pdf",    2026, 4),
    ("mai2026.pdf",      2026, 5),
    ("juin2026.pdf",     2026, 6),
]

# ── Default shift code metadata for auto-creation ────────────────────────
# When a code extracted from PDF isn't in the shift_types table, we insert
# it with sensible defaults based on known codes from the SHIFT_CODES_REFERENCE.
KNOWN_CODE_DEFAULTS = {
    # Operational
    "AMJP":  {"label": "C6 Jour Planifié",        "category": "operational", "gross_minutes": 730, "night_minutes": 0,   "color_hex": "#00BFFF", "is_operational": True},
    "AMJ1":  {"label": "C6 Jour 1",               "category": "operational", "gross_minutes": 730, "night_minutes": 0,   "color_hex": "#00BFFF", "is_operational": True},
    "AMJ2":  {"label": "C6 Jour 2",               "category": "operational", "gross_minutes": 730, "night_minutes": 0,   "color_hex": "#00BFFF", "is_operational": True},
    "AMNP":  {"label": "C6 Nuit Planifié",         "category": "operational", "gross_minutes": 710, "night_minutes": 600, "color_hex": "#00008B", "is_operational": True},
    "AMN1":  {"label": "C6 Nuit 1",               "category": "operational", "gross_minutes": 710, "night_minutes": 600, "color_hex": "#00008B", "is_operational": True},
    "AMN2":  {"label": "C6 Nuit 2",               "category": "operational", "gross_minutes": 710, "night_minutes": 600, "color_hex": "#00008B", "is_operational": True},
    "AMHS":  {"label": "C6 Horaire S",            "category": "operational", "gross_minutes": 600, "night_minutes": 0,   "color_hex": "#4169E1", "is_operational": True},
    "AMHR":  {"label": "C6 Horaire R",            "category": "operational", "gross_minutes": 480, "night_minutes": 0,   "color_hex": "#DC143C", "is_operational": True},
    # Admin
    "A":     {"label": "Administratif",            "category": "admin",      "gross_minutes": 480, "night_minutes": 0,   "color_hex": "#808080", "is_operational": False},
    "A1":    {"label": "Administratif 1",          "category": "admin",      "gross_minutes": 480, "night_minutes": 0,   "color_hex": "#808080", "is_operational": False},
    "A2":    {"label": "Administratif 2",          "category": "admin",      "gross_minutes": 480, "night_minutes": 0,   "color_hex": "#A9A9A9", "is_operational": False},
    "A3":    {"label": "Administratif 3",          "category": "admin",      "gross_minutes": 480, "night_minutes": 0,   "color_hex": "#999999", "is_operational": False},
    "A4":    {"label": "Administratif 4",          "category": "admin",      "gross_minutes": 480, "night_minutes": 0,   "color_hex": "#B0B0B0", "is_operational": False},
    "CSP":   {"label": "Chef Service/Planif",      "category": "admin",      "gross_minutes": 480, "night_minutes": 0,   "color_hex": "#696969", "is_operational": False},
    # Flexible
    "6FM":   {"label": "Flexible matin",           "category": "flexible",   "gross_minutes": 240, "night_minutes": 0,   "color_hex": "#D3D3D3", "is_operational": False},
    "6P1":   {"label": "Flexible jour complet",    "category": "flexible",   "gross_minutes": 480, "night_minutes": 0,   "color_hex": "#C0C0C0", "is_operational": False},
    # Absences
    "C":     {"label": "Congé / Repos",            "category": "absence",    "gross_minutes": 0,   "night_minutes": 0,   "color_hex": "#FFFFFF", "is_operational": False},
    "CMHN":  {"label": "Compensation nuit",        "category": "absence",    "gross_minutes": 0,   "night_minutes": 0,   "color_hex": "#FFD700", "is_operational": False},
    "E":     {"label": "École",                    "category": "absence",    "gross_minutes": 0,   "night_minutes": 0,   "color_hex": "#8B00FF", "is_operational": False},
    "FO9":   {"label": "Formation",                "category": "absence",    "gross_minutes": 540, "night_minutes": 0,   "color_hex": "#9370DB", "is_operational": False},
    "QC1":   {"label": "Congé prioritaire",        "category": "absence",    "gross_minutes": 0,   "night_minutes": 0,   "color_hex": "#FFA500", "is_operational": False},
    "VA":    {"label": "Vacances",                 "category": "absence",    "gross_minutes": 0,   "night_minutes": 0,   "color_hex": "#FFE600", "is_operational": False},
    "ANP":   {"label": "Accident non-prof",        "category": "absence",    "gross_minutes": 0,   "night_minutes": 0,   "color_hex": "#FF4500", "is_operational": False},
    "FIN":   {"label": "Fin tour nuit",            "category": "absence",    "gross_minutes": 0,   "night_minutes": 0,   "color_hex": "#CD5C5C", "is_operational": False},
    "M":     {"label": "Maladie",                  "category": "absence",    "gross_minutes": 0,   "night_minutes": 0,   "color_hex": "#FF6347", "is_operational": False},
    "COLL":  {"label": "Collège",                  "category": "absence",    "gross_minutes": 0,   "night_minutes": 0,   "color_hex": "#A0A0A0", "is_operational": False},
    "RECG":  {"label": "Récupération Jour",        "category": "absence",    "gross_minutes": 180, "night_minutes": 0,   "color_hex": "#FFD700", "is_operational": False},
    "RECN":  {"label": "Récupération Nuit",        "category": "absence",    "gross_minutes": 180, "night_minutes": 0,   "color_hex": "#FF8C00", "is_operational": False},
    "AP":    {"label": "Accident professionnel",   "category": "absence",    "gross_minutes": 0,   "night_minutes": 0,   "color_hex": "#FF0000", "is_operational": False},
    "MI":    {"label": "Militaire",                "category": "absence",    "gross_minutes": 0,   "night_minutes": 0,   "color_hex": "#556B2F", "is_operational": False},
    "MAT":   {"label": "Maternité",                "category": "absence",    "gross_minutes": 0,   "night_minutes": 0,   "color_hex": "#FF69B4", "is_operational": False},
    "PAT":   {"label": "Paternité",                "category": "absence",    "gross_minutes": 0,   "night_minutes": 0,   "color_hex": "#4682B4", "is_operational": False},
    "S":     {"label": "Service spécial",          "category": "operational","gross_minutes": 600, "night_minutes": 0,   "color_hex": "#BA55D3", "is_operational": True},
    "R":     {"label": "Horaire R",                "category": "operational","gross_minutes": 480, "night_minutes": 0,   "color_hex": "#DC143C", "is_operational": True},
    "RS":    {"label": "C6 Rapid Spécial",          "category": "operational","gross_minutes": 480, "night_minutes": 0,   "color_hex": "#E0115F", "is_operational": True},
    "AMBCE": {"label": "Ambulances CE",             "category": "operational","gross_minutes": 480, "night_minutes": 0,   "color_hex": "#20B2AA", "is_operational": True},
    "RAP":   {"label": "Rapport",                  "category": "admin",      "gross_minutes": 480, "night_minutes": 0,   "color_hex": "#708090", "is_operational": False},
    # Additional absence codes from SHIFT_CODES_REFERENCE
    "HC":    {"label": "Heures compensées",         "category": "absence",    "gross_minutes": 0,   "night_minutes": 0,   "color_hex": "#DAA520", "is_operational": False},
    "HS":    {"label": "Heures rendues (soldes)",    "category": "absence",    "gross_minutes": 0,   "night_minutes": 0,   "color_hex": "#BDB76B", "is_operational": False},
    "SM":    {"label": "Service Militaire",          "category": "absence",    "gross_minutes": 0,   "night_minutes": 0,   "color_hex": "#556B2F", "is_operational": False},
    "MR":    {"label": "Maternité/Repos",            "category": "absence",    "gross_minutes": 0,   "night_minutes": 0,   "color_hex": "#FF69B4", "is_operational": False},
    "QFO":   {"label": "Demande Formation",          "category": "absence",    "gross_minutes": 0,   "night_minutes": 0,   "color_hex": "#9370DB", "is_operational": False},
}


def run_extraction(pdf_path, year, month, mode="pixel"):
    """Extract entries from a monthly PDF using Camelot+OCR pipeline."""
    return ambulance_adapter.process_single_pdf(pdf_path, year, month)


def sync_shift_codes(extracted_codes: set):
    """Ensure all extracted shift codes exist in shift_types table."""
    res = supabase.table("shift_types").select("code").execute()
    db_codes = set(r["code"] for r in res.data)
    
    missing = extracted_codes - db_codes
    if not missing:
        return
    
    print(f"  ⚙ Inserting {len(missing)} missing shift codes: {sorted(missing)}")
    
    insert_rows = []
    for code in missing:
        defaults = KNOWN_CODE_DEFAULTS.get(code, {
            "label": f"Unknown ({code})",
            "category": "absence",
            "gross_minutes": 0,
            "night_minutes": 0,
            "color_hex": "#CCCCCC",
            "is_operational": False,
        })
        insert_rows.append({
            "code": code,
            "label": defaults["label"],
            "category": defaults["category"],
            "gross_minutes": defaults["gross_minutes"],
            "night_minutes": defaults["night_minutes"],
            "dressing_minutes": 0,
            "color_hex": defaults["color_hex"],
            "is_operational": defaults["is_operational"],
        })
    
    supabase.table("shift_types").upsert(insert_rows, on_conflict="code").execute()
    print(f"  ✓ Shift codes synced")


def upload_month(pdf_path, year, month, mode="pixel"):
    print(f"\n{'='*60}")
    print(f"  Processing {os.path.basename(pdf_path)} ({year}-{month:02d}) mode={mode}")
    print(f"{'='*60}")

    t0 = time.time()
    try:
        entries = run_extraction(pdf_path, year, month, mode)
    except Exception as e:
        print(f"  ❌ Extraction failed: {e}")
        import traceback; traceback.print_exc()
        return False

    elapsed = time.time() - t0
    print(f"  ✓ Extracted {len(entries)} entries in {elapsed:.1f}s")

    if not entries:
        print(f"  ⚠ No entries extracted, skipping")
        return False

    # Sync missing shift codes to DB
    all_codes = set(e["shift_code"] for e in entries if not e["shift_code"].startswith("UNK_"))
    sync_shift_codes(all_codes)

    # Get personnel mapping
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

    # Purge old imports for this month
    s_date = date(year, month, 1)
    e_date = s_date + relativedelta(months=1, days=-1)
    supabase.table("schedule_entries")\
        .delete()\
        .gte("entry_date", s_date.isoformat())\
        .lte("entry_date", e_date.isoformat())\
        .eq("source", "import")\
        .execute()
    print(f"  ✓ Purged old imports for {s_date} → {e_date}")

    # Deduplicate & upsert
    if payload:
        dedup = {}
        for row in payload:
            key = (row["personnel_id"], row["entry_date"])
            dedup[key] = row
        payload = list(dedup.values())
        
        # Upsert in batches of 500 to avoid payload limits
        batch_size = 500
        for i in range(0, len(payload), batch_size):
            batch = payload[i:i+batch_size]
            supabase.table("schedule_entries").upsert(
                batch, on_conflict="personnel_id,entry_date"
            ).execute()
        
        print(f"  ✓ Upserted {len(payload)} entries to Supabase")
    else:
        print(f"  ⚠ No entries to upsert")

    # Summary
    print(f"  Shift codes: {dict(sorted(by_shift.items()))}")
    print(f"  Employees matched: {len(by_employee)}")
    if unknowns:
        print(f"  Unknown codes skipped: {unknowns}")

    return True


def main():
    mode = "pixel"
    if "--mode" in sys.argv:
        idx = sys.argv.index("--mode")
        if idx + 1 < len(sys.argv):
            mode = sys.argv[idx + 1]

    print(f"Batch Upload — mode={mode}")
    print(f"Supabase URL: {SUPABASE_URL}")

    success = 0
    failed = 0

    for filename, year, month in MONTHS:
        pdf_path = os.path.join(PDF_DIR, filename)
        if not os.path.exists(pdf_path):
            print(f"\n  ⚠ Skipping {filename} — file not found at {pdf_path}")
            continue
        
        if upload_month(pdf_path, year, month, mode):
            success += 1
        else:
            failed += 1

    print(f"\n{'='*60}")
    print(f"  Done! {success} succeeded, {failed} failed")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
