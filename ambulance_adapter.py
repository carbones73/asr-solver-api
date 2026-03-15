from __future__ import annotations
"""
Adapter: ambulance_extractor → solver-api flat entry format
============================================================

Bridges the Camelot+pdfplumber-based ambulance_extractor package
(which returns Operatore/TurnoGiornaliero dataclasses) into the flat
dict format expected by main.py and batch_upload.py:

    [{"hierarchy_code": "03", "date": "2026-01-15", "shift_code": "D"}, …]

This is a drop-in replacement for the old extractor.process_single_pdf().
"""

import logging
import os
import sys
from typing import Dict, List
from unicodedata import normalize as _uninorm

# ── Ensure the ambulance_extractor package (sibling directory) is importable ──
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from ambulance_extractor import AmbulancePDFExtractor  # noqa: E402
from employees import EMPLOYEES  # noqa: E402

logger = logging.getLogger(__name__)


# ── Build name → hierarchy_code lookup (case-insensitive, accent-normalised) ──

def _normalise(s: str) -> str:
    """Lowercase, strip, NFC-normalise for accent-safe comparison."""
    return _uninorm("NFC", s.strip().lower())


def _build_name_map() -> Dict[str, str]:
    """Build multiple lookup keys for each employee.

    Keys generated:
    - full name as-is (normalised)
    - "Last First" → code
    - "First Last" → code
    - last name only (if unique)
    """
    by_full: Dict[str, str] = {}
    by_last: Dict[str, List[str]] = {}

    for code, info in EMPLOYEES.items():
        name = info["name"]
        norm = _normalise(name)
        by_full[norm] = code

        parts = name.split()
        if len(parts) >= 2:
            # "Last First" → also store "First Last"
            reversed_name = _normalise(" ".join(parts[1:]) + " " + parts[0])
            by_full[reversed_name] = code

            last = _normalise(parts[0])
            by_last.setdefault(last, []).append(code)

    # Add unique last-name lookups
    for last, codes in by_last.items():
        if len(codes) == 1:
            by_full[last] = codes[0]

    return by_full


_NAME_MAP = _build_name_map()


def _resolve_hierarchy_code(operator_name: str) -> str | None:
    """Return hierarchy code for an operator name, or None if unknown."""
    norm = _normalise(operator_name)
    if norm in _NAME_MAP:
        return _NAME_MAP[norm]

    # Fuzzy: try matching by last name only (first word of the input)
    parts = norm.split()
    if parts:
        last = parts[0]
        if last in _NAME_MAP:
            return _NAME_MAP[last]

    return None


# ── Shift code normalisation ─────────────────────────────────────────────────

# The ambulance_extractor may produce codes that differ slightly from what the
# solver-api expects. This mapping corrects the most common variations.
# Normalisation table per SHIFT_CODES_REFERENCE.md
# Maps raw PDF / extractor codes to the canonical codes used by the solver.
_CODE_ALIASES = {
    # Day variants → AMJP (same 06:30–18:40 shift)
    "AMJ":  "AMJP",
    "AMJ1": "AMJP",
    "AMJ2": "AMJP",
    # Night variants → AMNP (same 18:40–06:30 shift)
    "AMN":  "AMNP",
    "AMN1": "AMNP",
    "AMN2": "AMNP",
    # Special hour R → AMHS (same type)
    "AMHR": "AMHS",
    # Rapid Spécial numbered variants → RS
    "RS5":  "RS",  "RS6":  "RS",  "RS7":  "RS",
    "RS8":  "RS",  "RS9":  "RS",  "RS10": "RS",
    # Absence aliases
    "C1":   "QC1",   # Congé prioritaire
    "ML":   "M",     # Maladie (alias court)
}


def _normalise_shift_code(raw: str) -> str:
    """Normalise a shift code from the extractor to the solver-api convention."""
    code = raw.strip().upper()
    return _CODE_ALIASES.get(code, code)


# ── Public API ────────────────────────────────────────────────────────────────

def process_single_pdf(pdf_path: str, year: int, month: int) -> List[dict]:
    """Extract shift entries from an ASR planning PDF.

    Uses the Camelot+pdfplumber ambulance_extractor package.

    Returns a list of dicts identical to what the old extractor.py produced:
        [{"hierarchy_code": "03", "date": "2026-01-15", "shift_code": "D"}, …]
    """
    extractor = AmbulancePDFExtractor(
        file_pdf=pdf_path,
        anno=year,
        debug=False,
    )

    turni = extractor.estrai()
    logger.info("ambulance_extractor returned %d turni", len(turni))

    entries: List[dict] = []
    unresolved_names: set = set()

    for turno in turni:
        hierarchy_code = _resolve_hierarchy_code(turno.nome_operatore)

        if hierarchy_code is None:
            unresolved_names.add(turno.nome_operatore)
            continue

        # Each TurnoGiornaliero can have multiple shift codes for the same day.
        # We take the first meaningful one (the primary code).
        codes = turno.codici_turno
        if not codes:
            continue

        shift_code = _normalise_shift_code(codes[0])

        entries.append({
            "hierarchy_code": hierarchy_code,
            "date": turno.data,           # already YYYY-MM-DD from extractor
            "shift_code": shift_code,
        })

    if unresolved_names:
        logger.warning(
            "Could not resolve %d operator name(s) to hierarchy codes: %s",
            len(unresolved_names),
            sorted(unresolved_names),
        )

    # Deduplicate: keep last entry for each (hierarchy_code, date) pair
    seen: Dict[tuple, int] = {}
    for idx, e in enumerate(entries):
        key = (e["hierarchy_code"], e["date"])
        seen[key] = idx

    deduped = [entries[i] for i in sorted(seen.values())]

    logger.info(
        "Adapter produced %d entries (%d raw, %d after dedup)",
        len(deduped), len(entries), len(deduped),
    )

    return deduped
