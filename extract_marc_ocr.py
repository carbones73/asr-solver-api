#!/usr/bin/env python3
"""Extract Marc François shifts using ambulance_extractor (OCR pipeline)."""
import sys, os, logging
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s: %(message)s')

from ambulance_adapter import process_single_pdf

pdf_path = "/Users/stefano/.gemini/antigravity/scratch/ASR Planification/janvier2026.pdf"

print(f"PDF: {pdf_path}")
print(f"Using: ambulance_adapter (OCR pipeline)\n")

entries = process_single_pdf(pdf_path, year=2026, month_num=1)

print(f"\nTotal entries: {len(entries)}")

marc_entries = [e for e in entries if e["hierarchy_code"] == "00"]
marc_10 = [e for e in marc_entries if int(e["date"].split("-")[2]) <= 10]

print(f"Marc François total: {len(marc_entries)}")
print(f"Marc François days 1-10: {len(marc_10)}\n")

if marc_10:
    print("Marc François — Janvier 2026, jours 1–10 (OCR):")
    print("-" * 50)
    for e in sorted(marc_10, key=lambda x: x["date"]):
        print(f"  {e['date']}: {e['shift_code']}")
else:
    print("No entries found for Marc François")
    if entries:
        codes = sorted(set(e['hierarchy_code'] for e in entries))
        print(f"Available codes: {codes}")
        print(f"\nSample entries:")
        for e in entries[:5]:
            print(f"  {e}")
