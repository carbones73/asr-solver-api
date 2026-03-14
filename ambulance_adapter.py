"""
Adapter for ambulance_extractor
================================

Wraps ambulance_extractor.py to expose the same interface as extractor.py:
    process_single_pdf(pdf_path, year, month_num)  →  [{hierarchy_code, date, shift_code}, …]

This allows the solver-api endpoints to switch between pixel-based and
OCR-based extraction transparently.
"""

from __future__ import annotations

import logging
from typing import Dict, List, Any

from extractor import EMPLOYEES  # reuse the canonical employee registry

import ambulance_extractor as amb

logger = logging.getLogger(__name__)


# ── Reverse lookup: employee name → hierarchy code ────────────────────────
_NAME_TO_CODE: Dict[str, str] = {}
for _code, _info in EMPLOYEES.items():
    _NAME_TO_CODE[_info["name"]] = _code

# Normalised variants to handle OCR misreadings (accents, spacing, case)
_NAME_TO_CODE_NORMALISED: Dict[str, str] = {}
for _name, _code in _NAME_TO_CODE.items():
    _NAME_TO_CODE_NORMALISED[_name.lower().strip()] = _code


# ── Shift code normalisation ─────────────────────────────────────────────
# The ambulance extractor returns raw OCR codes (e.g. "AMJ1", "AMN2", "ML").
# The solver expects normalised codes matching extractor.py conventions.
_CODE_ALIASES: Dict[str, str] = {
    "AMJ1": "AMJP",
    "AMJ2": "AMJP",
    "AMN1": "AMNP",
    "AMN2": "AMNP",
    "AMHR": "AMHS",
    "ML":   "M",
    "C1":   "QC1",
    "RS5":  "RS",
    "RS6":  "RS",
    "RS7":  "RS",
    "RS8":  "RS",
    "RS9":  "RS",
    "RS10": "RS",
}


def _normalise_shift_code(code: str) -> str:
    """Normalise a raw OCR shift code to the solver convention."""
    return _CODE_ALIASES.get(code, code)


def _resolve_name(name: str) -> str | None:
    """Resolve an operator name to a hierarchy code.

    Tries exact match first, then normalised (case-insensitive, stripped).
    Returns None if no match found.
    """
    if name in _NAME_TO_CODE:
        return _NAME_TO_CODE[name]
    return _NAME_TO_CODE_NORMALISED.get(name.lower().strip())


def process_single_pdf(
    pdf_path: str,
    year: int,
    month_num: int,
) -> List[Dict[str, Any]]:
    """Extract shift entries from an ambulance planning PDF.

    Uses the ambulance_extractor pipeline (Camelot + OCR + pHash) and
    converts records to the solver-api format.

    Parameters
    ----------
    pdf_path : str
        Path to the PDF file.
    year : int
        Year of the planning period.
    month_num : int
        Month number (1–12).

    Returns
    -------
    list of dict
        Each dict has keys ``hierarchy_code``, ``date``, ``shift_code``.
    """
    # 1. Render pages
    pages = amb.extract_pages_pdf(pdf_path, dpi=200)

    all_records: List[Dict[str, Any]] = []

    for page_idx, page_img in enumerate(pages):
        # 2. Extract legend and schedule mapping
        legend_map, schedule_map = amb.extract_legend_from_page(page_img)

        # 3. Build raw assignments
        raw_assignments = amb.build_assignments_for_page(
            pdf_path=pdf_path,
            page_idx=page_idx,
            page_image=page_img,
            legend_map=legend_map,
            schedule_map=schedule_map,
            year_hint=year,
        )

        # 4. Convert each assignment to solver format
        for a in raw_assignments:
            name = a.get("nome_operatore", "")
            hierarchy_code = _resolve_name(name)
            if hierarchy_code is None:
                logger.debug(
                    "OCR extractor: skipping unknown operator %r (page %d)",
                    name, page_idx + 1,
                )
                continue

            entry_date = a.get("data")
            if not entry_date:
                continue

            # Determine shift code from OCR-detected codes
            # The ambulance extractor doesn't put codes directly in the
            # assignment; instead icons are matched.  We derive the code
            # from the icon meanings or from the schedule_map keys.
            shift_code = _derive_shift_code(a, schedule_map)
            if shift_code is None:
                continue

            shift_code = _normalise_shift_code(shift_code)

            all_records.append({
                "hierarchy_code": hierarchy_code,
                "date": entry_date,
                "shift_code": shift_code,
            })

    logger.info(
        "OCR extractor produced %d entries for %d/%d",
        len(all_records), month_num, year,
    )
    return all_records


def _derive_shift_code(
    assignment: Dict[str, Any],
    schedule_map: Dict[str, tuple],
) -> str | None:
    """Derive the best shift code from an ambulance extractor assignment.

    Priority:
    1. If turno_inizio/turno_fine are set *and* appear in schedule_map → use
       the corresponding key.
    2. If significato_icone contains a recognisable label → map it.
    3. Fall back to the first icon meaning.
    """
    start = assignment.get("turno_inizio")
    end = assignment.get("turno_fine")

    # If the ambulance extractor already resolved start/end times, reverse-
    # lookup the code from schedule_map.
    if start and end:
        for code, (st, et) in schedule_map.items():
            if st == start and et == end:
                return code

    # Try icon meanings
    meanings = assignment.get("significato_icone", [])
    for meaning in meanings:
        # Icon labels from the legend often contain the code directly
        # e.g. "AMJ1 | 23 C6 Jour 1 ; 0630-1840"
        for known in schedule_map:
            if known in meaning:
                return known

    # If we have any meaning at all, try to extract a shift code from it
    if meanings:
        import re
        for meaning in meanings:
            match = re.search(r'\b([A-Z]{1,5}\d{0,2})\b', meaning)
            if match:
                return match.group(1)

    return None
