import pymupdf
import math
from datetime import date

EMPLOYEES = {
    "00": {"name": "François Marc",             "type": "cs",  "fte": 100},
    "01": {"name": "Levet Jason",               "type": "ad",  "fte": 80},
    "02": {"name": "Becircic Narda",            "type": "ad",  "fte": 100},
    "03": {"name": "Carbone Stefano",           "type": "ad",  "fte": 100},
    "04": {"name": "Chalon Bernard",            "type": "ad",  "fte": 100},
    "05": {"name": "Cottet Loan",               "type": "ad",  "fte": 100},
    "06": {"name": "Fuochi Yasmine",            "type": "ad",  "fte": 80},
    "07": {"name": "Allemann Laurent",          "type": "ad",  "fte": 80},
    "08": {"name": "Bangerter Louison",         "type": "ad",  "fte": 100},
    "09": {"name": "Bourdon Olivier",           "type": "ad",  "fte": 100},
    "10": {"name": "Burkhart Edern",            "type": "ad",  "fte": 100},
    "11": {"name": "Denoréaz Olivier",          "type": "ad",  "fte": 100},
    "12": {"name": "Gaillard Nicolas",          "type": "ad",  "fte": 100},
    "13": {"name": "Groux Sébastien",           "type": "ad",  "fte": 100},
    "14": {"name": "Guillaume-Gentil Tiffany",  "type": "ad",  "fte": 70},
    "15": {"name": "Gutierrez Pascal",          "type": "ta",  "fte": 100},
    "16": {"name": "Jorand Marie-Hélène",       "type": "ad",  "fte": 50},
    "17": {"name": "Kaech Sébastien",           "type": "ad",  "fte": 100},
    "18": {"name": "Métraux François",          "type": "ad",  "fte": 80},
    "19": {"name": "Monachon Christian",         "type": "ad",  "fte": 100},
    "20": {"name": "Pache Sébastien",           "type": "ad",  "fte": 100},
    "21": {"name": "Paux Christian",            "type": "ad",  "fte": 50},
    "22": {"name": "Rivaletto Adrien",          "type": "ad",  "fte": 100},
    "23": {"name": "Roubaty Kelly",             "type": "ad",  "fte": 70},
    "24": {"name": "Simon Daniel",              "type": "ad",  "fte": 100},
    "25": {"name": "Thürler Baptiste",          "type": "ad",  "fte": 80},
    "26": {"name": "Troendle Vyacheslav",       "type": "ad",  "fte": 100},
    "27": {"name": "Vaudroz Jeremy",            "type": "ad",  "fte": 100},
    "28": {"name": "Vauthey Laurent",           "type": "ad",  "fte": 80},
    "29": {"name": "Vez Marc",                  "type": "ad",  "fte": 80},
    "30": {"name": "Wiasemsky David",           "type": "ad",  "fte": 80},
    "31": {"name": "Andler Thomas",             "type": "ta",  "fte": 50},
    "32": {"name": "Curty Doris",               "type": "ta",  "fte": 50},
    "33": {"name": "Lebrun Eddy",               "type": "ta",  "fte": 100},
    "34": {"name": "Lopez Angel",               "type": "ta",  "fte": 100},
    "35": {"name": "Magnin Sandrine",           "type": "ta",  "fte": 50},
    "36": {"name": "Pilet Frédéric",            "type": "ta",  "fte": 80},
    "37": {"name": "Spichiger Thierry",         "type": "aux", "fte": 0},
    "38": {"name": "Voutaz Noémie",             "type": "aux", "fte": 0},
    "39": {"name": "Debaud Laure",              "type": "sec", "fte": 80},
}

# ── Known PDF colors: (R, G, B, shift_code) ────────────────────────────────
# Each entry is a color observed in the ASR planning PDFs.
# classify_center_pixel() finds the nearest match by Euclidean distance.
# Last calibrated: 2026-03-12 from scan of 6 PDFs (Jan–Jun 2026)
#
# ══ Official PDF Legend (from page footers) ═══════════════════════════════
# FONCTIONS (turni operativi):
#   6P1     = C6 Flexible jour complet   07:00-11:00 / 13:30-17:30
#   A1      = Administratif              17:00-19:15
#   A2      = Administratif              08:00-16:00
#   A3      = Administratif              13:00-23:00
#   A4      = Administratif              13:00-23:40
#   AMHR    = C6 Horaire R               08:55-16:55
#   AMHS    = C6 Horaire S               09:55-19:55
#   AMJ1    = C6 Jour 1                  06:30-18:40
#   AMJ2    = C6 Jour 2                  06:30-18:40
#   AMJP    = C6 Jour Planifié           06:30-18:40
#   AMN1    = C6 Nuit 1                  18:40-06:30
#   AMN2    = C6 Nuit 2                  18:40-06:30
#   RS5–RS10 = Rapid Spécial (varianti)  vari orari
#
# ABSENCES:
#   C       = Congé, repos
#   C1      = Congé prioritaire, repos   → mapped to QC1
#   CMHN    = Compensation majoration nuit
#   E       = Ecole (ambulance)
#   FO9     = Formation, cours (9h)
#   ML      = Maladie non prof.          → mapped to M
#   VA      = Vacances
#   HC      = Heures compensées (soldes heures)
#   ANP     = Accident non-professionnel
#   AMBCE   = Ambulances CE
#
# MARQUAGES (overlay, non turni):
#   PE      = C6 - Permutation effectuée ✅ (magenta overlay)
#   RJ      = C6 - Responsable du jour   (overlay)
#
# PIXEL-BASED CODE MAPPING NOTES:
#   - AMJ1/AMJ2 share same color as AMJP → all mapped to "AMJP"
#   - AMN1/AMN2 share same color as AMNP → all mapped to "AMNP"
#   - RS5–RS10 share same orange color  → all mapped to "RS"
#   - AMHR similar lilac as AMHS        → mapped to "AMHS"
#   - ML (new legend code) = M          → mapped to "M"
#   - C1 (new legend code)              → mapped to "QC1"
#   - PE/RJ are marquages, not shift replacements
# ══════════════════════════════════════════════════════════════════════════
KNOWN_COLORS = [
    # ── FONCTIONS (turni operativi) ─────────────────────────────────────
    # AMNP / AMN1 / AMN2 (C6 Nuit) — dark blue / near-black
    (51,  54,  99,  "AMNP"),
    (4,   4,   14,  "AMNP"),
    (0,   5,   19,  "AMNP"),
    (0,   20,  34,  "AMNP"),
    (10,  10,  30,  "AMNP"),
    (0,   0,   10,  "AMNP"),
    (5,   5,   20,  "AMNP"),
    (71,  58,  140, "AMNP"),   # from Feb/Mar PDFs — purple-tint night
    # AMJP / AMJ1 / AMJ2 (C6 Jour) — cyan / teal / bleu-vert
    (88,  157, 232, "AMJP"),
    (75,  157, 230, "AMJP"),
    (0,   82,  68,  "AMJP"),
    (0,   117, 105, "AMJP"),
    (0,   150, 200, "AMJP"),
    (20,  70,  60,  "AMJP"),
    (70,  226, 255, "AMJP"),   # bright cyan — 148 placements across PDFs
    (0,   149, 144, "AMJP"),   # dark teal variant
    (0,   255, 255, "AMJP"),   # pure aqua (page-render artefact)
    (20,  250, 250, "AMJP"),   # bright aqua variant
    (10,  248, 248, "AMJP"),   # bright aqua variant
    (0,   250, 240, "AMJP"),   # teal-aqua variant
    # AMHS / AMHR (C6 Horaire S/R) — mauve / lilas / violet pâle
    (214, 187, 215, "AMHS"),
    (186, 156, 211, "AMHS"),
    (204, 204, 255, "AMHS"),
    (170, 150, 200, "AMHS"),
    (140, 130, 190, "AMHS"),
    (50,  110, 130, "AMHS"),
    # R (Rapid Responder) — rouge vif
    (252, 33,  0,   "R"),      # bright red — 68 placements
    (240, 30,  0,   "R"),
    (255, 40,  10,  "R"),
    # RS / RS5–RS10 (Rapid Spécial) — orange vif
    (255, 127, 0,   "RS"),     # bright orange — 68 placements
    (255, 120, 0,   "RS"),
    # 6P1 (C6 Flexible jour complet) — gris moyen
    (170, 170, 170, "6P1"),
    (155, 155, 155, "6P1"),
    # 6FM (Flexible matin) — gris clair
    (210, 210, 210, "6FM"),
    (195, 195, 195, "6FM"),
    (176, 196, 194, "6FM"),    # from PDF scan (blue-tint grey)
    (170, 194, 204, "6FM"),    # from PDF scan (blue-tint grey)
    (200, 255, 214, "6FM"),    # from PDF scan (light green-grey)
    # A1 (Administratif) — gris très foncé
    (70,  70,  70,  "A1"),
    (55,  55,  55,  "A1"),
    # A2 (Administratif 08:00-16:00) — gris foncé
    (120, 120, 120, "A2"),
    (105, 105, 105, "A2"),
    (93,  107, 146, "A2"),     # from PDF scan (blue-tint grey)
    # A3 (Administratif 13:00-23:00) — gris bleuté (estimated, rare)
    (85,  90,  110, "A3"),
    # A4 (Administratif 13:00-23:40) — gris bleuté foncé (estimated, rare)
    (75,  80,  100, "A4"),
    # AMBCE (Ambulances CE) — same tones as AMJP (estimated)
    # NOTE: AMBCE may appear as text overlay on a distinct background
    #       — if seen, will need recalibration

    # ── ABSENCES ─────────────────────────────────────────────────────────
    # C (Congé, repos) — vert
    (0,   128, 0,   "C"),
    (0,   100, 0,   "C"),
    (30,  140, 30,  "C"),
    (154, 255, 182, "C"),      # light green — 6 placements
    (121, 255, 185, "C"),      # mint green — 1 placement
    (150, 255, 206, "C"),      # pale mint — 3 placements
    (106, 250, 102, "C"),      # bright lime-green (congé variant)
    # QC1 / C1 (Congé prioritaire) — orange brun
    (190, 145, 77,  "QC1"),
    (198, 149, 80,  "QC1"),
    (188, 143, 74,  "QC1"),
    (175, 130, 60,  "QC1"),
    (191, 140, 70,  "QC1"),
    (180, 135, 65,  "QC1"),
    (200, 155, 85,  "QC1"),
    (165, 120, 55,  "QC1"),
    (225, 116, 83,  "QC1"),    # from PDF scan (orange-red QC1)
    (218, 109, 70,  "QC1"),    # from PDF scan
    (194, 154, 103, "QC1"),    # from PDF scan (tan/sandy QC1)
    # CMHN (Compensation majoration nuit) — jaune vif
    (222, 207, 52,  "CMHN"),
    (230, 200, 40,  "CMHN"),
    (255, 230, 50,  "CMHN"),
    (255, 252, 8,   "CMHN"),   # from PDF scan
    (255, 192, 23,  "CMHN"),   # from PDF scan
    (255, 194, 36,  "CMHN"),   # from PDF scan
    (255, 248, 69,  "CMHN"),   # from PDF scan
    # VA (Vacances) — jaune-orangé
    (255, 217, 105, "VA"),
    (255, 200, 80,  "VA"),
    (240, 190, 60,  "VA"),
    (236, 211, 131, "VA"),     # from PDF scan
    (255, 230, 129, "VA"),     # from PDF scan
    (248, 231, 127, "VA"),     # from PDF scan
    (232, 255, 117, "VA"),     # from PDF scan (yellow-green VA)
    # VA — warm orange-yellow (G < 195) sampled from Jan 2026 Marc François
    (255, 177, 40,  "VA"),     # from Jan scan — days 2-4
    (255, 180, 43,  "VA"),     # from Jan scan — day 11
    (255, 176, 41,  "VA"),     # from Jan scan — day 10
    (255, 175, 40,  "VA"),     # from Jan scan — day 24
    (254, 179, 39,  "VA"),     # from Jan scan — day 31
    (255, 178, 38,  "VA"),     # warm orange variant
    (253, 174, 37,  "VA"),     # warm orange variant
    # E (École ambulance) — olive / brun-vert
    (64,  90,  1,   "E"),
    (152, 157, 57,  "E"),
    (180, 192, 0,   "E"),
    (125, 139, 25,  "E"),
    (100, 120, 30,  "E"),
    (93,  221, 50,  "E"),      # bright green — école variant
    (4,   255, 3,   "E"),      # pure bright green (école)
    (1,   245, 2,   "E"),      # bright lime-green (école)
    # FO9 (Formation, cours 9h) — violet / pourpre
    (160, 120, 200, "FO9"),
    (147, 112, 219, "FO9"),
    # M / ML (Maladie non prof.) — rouge foncé
    (112, 6,   6,   "M"),
    (138, 0,   0,   "M"),
    (34,  0,   0,   "M"),
    (135, 40,  0,   "M"),
    (150, 20,  20,  "M"),
    (156, 0,   0,   "M"),      # from PDF scan
    # ANP (Accident non-professionnel) — orange-rouge
    (191, 81,  44,  "ANP"),
    (200, 80,  40,  "ANP"),
    (195, 85,  58,  "ANP"),    # from PDF scan
    (204, 95,  62,  "ANP"),    # from PDF scan
    (210, 100, 67,  "ANP"),    # from PDF scan
    # AP (Accident professionnel — not in legend, kept for old PDFs) — rose/saumon
    (255, 187, 172, "AP"),     # salmon pink — 6 placements
    (255, 180, 165, "AP"),
    # HC (Heures compensées / soldes heures) — vert pâle (estimated, rare)
    # NOTE: HC may share green tones with C — needs calibration if seen

    # ── MARQUAGES (overlay, non pas des turni) ──────────────────────────
    # PE (C6 - Permutation effectuée ✅) — magenta / fuchsia
    (129, 0,   127, "PE"),      # deep magenta — Curty Doris Jan 2026
    # RJ (C6 - Responsable du jour) — no distinct color observed yet
    # NOTE: RJ is typically a text overlay, not a cell background color
]

# Pre-sort threshold for quick white/background rejection
_COLOR_MATCH_THRESHOLD = 65  # Increased to 65 for page-render color variance

# ── Shift code sets for text-overlay extraction ─────────────────────────────
# Codes that appear as text overlays printed directly on grid cells.
_TEXT_OVERLAY_SHIFT_CODES = frozenset({
    # Operational shifts
    "6P1", "6FM", "A1", "A2", "A3", "A4",
    "AMHR", "AMHS", "AMJ1", "AMJ2", "AMJP",
    "AMN1", "AMN2", "AMNP",
    "RS", "RS5", "RS6", "RS7", "RS8", "RS9", "RS10",
    "R", "AMBCE", "CSP",
    # Absence / overlay codes that may appear as text
    "C", "C1", "CMHN", "VA", "E", "FO9",
    "ML", "M", "HC", "ANP", "AP",
    "PE", "RJ",
})


def _normalize_shift_code(code):
    """Normalize variant shift codes to canonical forms used by the solver."""
    if code in ("AMJ1", "AMJ2"):
        return "AMJP"
    if code in ("AMN1", "AMN2"):
        return "AMNP"
    if code == "AMHR":
        return "AMHS"
    if code.startswith("RS") and code != "RS":
        return "RS"
    if code == "C1":
        return "QC1"
    if code == "ML":
        return "M"
    return code


def classify_center_pixel(r, g, b):
    """Classify a pixel RGB value to a shift code using nearest-neighbor matching."""
    brightness = (r + g + b) / 3

    # Fast path: white/near-white background → ignore
    # Also catch light-cyan backgrounds (186-211, 255, 255) that are PDF bg
    if brightness > 230:
        return "IGNORE"

    # Near-black: dark blue tint = AMNP, pure dark = A1
    if brightness < 25:
        if b > r + 5:
            return "AMNP"
        return "A1"

    # Euclidean nearest-neighbor search
    best_code = None
    best_dist = float("inf")
    for cr, cg, cb, code in KNOWN_COLORS:
        dist = math.sqrt((r - cr) ** 2 + (g - cg) ** 2 + (b - cb) ** 2)
        if dist < best_dist:
            best_dist = dist
            best_code = code

    if best_dist <= _COLOR_MATCH_THRESHOLD:
        return best_code

    return f"UNK_{r}_{g}_{b}"


def process_single_pdf(pdf_path, year, month_num):
    """Extract shift entries from an ASR planning PDF using page-render pixel sampling.

    The PDF consists of multiple pages, each containing a grid of colored cells
    (baked into large background strip images). Employee names appear as text in
    the left column, and day numbers appear as text in the header row.

    Strategy:
    1. Iterate over ALL pages.
    2. Map employee names (text) → hierarchy codes via the EMPLOYEES dict.
    3. Identify day column x-positions from header text.
    4. Render each page at high DPI and sample the pixel color at each
       (employee-row, day-column) grid intersection.
    5. Classify each sampled color to a shift code.
    """
    doc = pymupdf.open(pdf_path)

    # Build reverse lookup: employee name → hierarchy code
    _name_to_code = {}
    for code, info in EMPLOYEES.items():
        _name_to_code[info["name"]] = code

    # Names that should NOT be treated as employee names
    _SKIP_TEXTS = frozenset({
        "ad", "ta", "cs", "sec", "aux", "", "RS",
        "Fonctions:", "Absences:", "Marquages:",
    })

    ZOOM = 4.0  # ~288 DPI — good balance of accuracy vs memory
    cell_map = {}  # (hierarchy_code, day) → shift_code
    unk_colors_found = {}

    for page_idx in range(len(doc)):
        page = doc[page_idx]

        # ── Render the page as a high-resolution pixmap ──────────────────
        mat = pymupdf.Matrix(ZOOM, ZOOM)
        pix = page.get_pixmap(matrix=mat)

        # ── Extract text spans ───────────────────────────────────────────
        dict_blocks = page.get_text("dict")["blocks"]
        all_spans = []
        for b in dict_blocks:
            if b["type"] != 0:
                continue
            for l in b["lines"]:
                for s in l["spans"]:
                    all_spans.append(s)

        # ── Find day-column x-positions from header text ─────────────────
        # Day numbers ("1.", "2.", … "31.") appear in the header area (y ~ 85-100)
        day_cols = {}
        for s in all_spans:
            txt = s["text"].strip().rstrip(".")
            bbox = s["bbox"]
            cy = (bbox[1] + bbox[3]) / 2
            if txt.isdigit() and 1 <= int(txt) <= 31 and 70 < cy < 100:
                day_num = int(txt)
                cx = (bbox[0] + bbox[2]) / 2
                if day_num not in day_cols:
                    day_cols[day_num] = cx

        if not day_cols:
            continue  # page has no grid header; skip

        # ── Find employee-row y-positions from left-column name text ─────
        emp_rows = []  # list of (hierarchy_code, cell_center_y)
        for s in all_spans:
            txt = s["text"].strip()
            bbox = s["bbox"]

            # Left column, within the grid area (below header, above footer)
            if bbox[0] >= 95 or bbox[1] <= 100 or bbox[1] >= 420:
                continue
            if txt in _SKIP_TEXTS:
                continue
            # Skip percentage strings like "80%", star annotations, and short tokens
            if txt.endswith("%") or txt.endswith("*") or len(txt) <= 3:
                continue
            # Skip known non-name patterns
            if any(txt.startswith(p) for p in ("AM", "6P", "6F", "C1", "FO", "PE", "Date", "RS")):
                continue

            emp_code = _name_to_code.get(txt)
            if emp_code is None:
                continue

            # The colored cell center is a few points below the name baseline
            cell_y = (bbox[1] + bbox[3]) / 2 + 5
            emp_rows.append((emp_code, cell_y))

        # ── Extract text overlays (shift codes printed on cells) ──────────
        # Text overlays are the authoritative source: "6P1", "AMJ1", etc.
        # are printed as text directly on grid cells (often on white bg).
        text_overlay_map = {}  # (emp_code, day) → shift_code

        for s in all_spans:
            txt = s["text"].strip()
            if txt not in _TEXT_OVERLAY_SHIFT_CODES:
                continue

            bbox = s["bbox"]
            span_cx = (bbox[0] + bbox[2]) / 2
            span_cy = (bbox[1] + bbox[3]) / 2

            # Must be inside the grid body (right of name column, below header)
            if span_cx < 95 or span_cy < 100 or span_cy > 420:
                continue

            # Find closest employee row by y-position
            closest_emp = None
            min_emp_dist = 15  # max y-distance tolerance (points)
            for emp_code, row_y in emp_rows:
                dist = abs(span_cy - row_y)
                if dist < min_emp_dist:
                    min_emp_dist = dist
                    closest_emp = emp_code

            # Find closest day column by x-position
            closest_day = None
            min_day_dist = 10  # max x-distance tolerance (points)
            for day, col_x in day_cols.items():
                dist = abs(span_cx - col_x)
                if dist < min_day_dist:
                    min_day_dist = dist
                    closest_day = day

            if closest_emp and closest_day:
                key = (closest_emp, closest_day)
                normalized = _normalize_shift_code(txt)
                # Text overlay always wins (most reliable source)
                text_overlay_map[key] = normalized

        # ── Sample pixel color at each grid intersection ─────────────────
        for emp_code, row_y in emp_rows:
            emp_type = EMPLOYEES[emp_code]["type"]

            for day, col_x in day_cols.items():
                key = (emp_code, day)
                if key in cell_map:
                    continue  # already have an entry from a previous page

                # Priority 1: text overlay (authoritative)
                if key in text_overlay_map:
                    shift_code = text_overlay_map[key]
                    # cs/sec employees don't work nights — reclassify AMNP → A1
                    if emp_type in ("cs", "sec") and shift_code == "AMNP":
                        shift_code = "A1"
                    cell_map[key] = shift_code
                    continue

                # Priority 2: icon histogram detection for C/C1/VA raster
                # sun icons.  These codes don't appear as text – they're
                # JPEG pixels in the strip images.  Detect by counting
                # "warm" (orange/yellow) pixels across the full cell.
                icon_detected = False
                warm_count = 0
                total_count = 0
                CELL_HALF_W = 8   # ±8 pt ~ 16 pt cell width
                CELL_HALF_H = 5   # ±5 pt ~ 10 pt cell height
                STEP = 0.5        # sample every 0.5 pt (2× ZOOM)

                dx_pt = -CELL_HALF_W
                while dx_pt <= CELL_HALF_W:
                    dy_pt = -CELL_HALF_H
                    while dy_pt <= CELL_HALF_H:
                        sx = int((col_x + dx_pt) * ZOOM)
                        sy = int((row_y + dy_pt) * ZOOM)
                        sx = max(0, min(pix.width - 1, sx))
                        sy = max(0, min(pix.height - 1, sy))
                        p = pix.pixel(sx, sy)
                        pr, pg, pb = p[0], p[1], p[2]
                        total_count += 1
                        # Warm = orange/yellow sun body + rays
                        if pr > 180 and pg > 80 and pb < 120 and (pr - pb) > 80:
                            warm_count += 1
                        dy_pt += STEP
                    dx_pt += STEP

                warm_ratio = warm_count / total_count if total_count else 0

                if warm_ratio > 0.10:
                    # Sun icon detected → classify C vs C1 vs VA by
                    # sampling corner pixels (background behind the icon)
                    icon_detected = True
                    corner_r, corner_g, corner_b = 0, 0, 0
                    corner_n = 0
                    for cdx in (-CELL_HALF_W, -CELL_HALF_W + 1):
                        for cdy in (-CELL_HALF_H, -CELL_HALF_H + 1):
                            cx = int((col_x + cdx) * ZOOM)
                            cy = int((row_y + cdy) * ZOOM)
                            cx = max(0, min(pix.width - 1, cx))
                            cy = max(0, min(pix.height - 1, cy))
                            cp = pix.pixel(cx, cy)
                            brightness = (cp[0] + cp[1] + cp[2]) / 3
                            if brightness < 240:  # skip pure white borders
                                corner_r += cp[0]
                                corner_g += cp[1]
                                corner_b += cp[2]
                                corner_n += 1

                    if corner_n > 0:
                        cr = corner_r // corner_n
                        cg = corner_g // corner_n
                        cb = corner_b // corner_n
                        # VA: blue-grey background (R<160, B>150)
                        if cr < 160 and cb > 150:
                            shift_code = "VA"
                        # C: bright yellow (R>230, G>230, B<50)
                        elif cr > 230 and cg > 230 and cb < 60:
                            shift_code = "C"
                        else:
                            # C1 / default congé: amber/orange background
                            shift_code = "C1"
                    else:
                        shift_code = "C"  # fallback if corners are white

                if not icon_detected:
                    # Priority 3: pixel color (fallback for solid-fill cells)
                    px = int(col_x * ZOOM)
                    py = int(row_y * ZOOM)
                    px = max(0, min(pix.width - 1, px))
                    py = max(0, min(pix.height - 1, py))

                    pixel = pix.pixel(px, py)
                    r, g, b = pixel[0], pixel[1], pixel[2]
                    shift_code = classify_center_pixel(r, g, b)

                    # If center pixel is ambiguous, sample a 5×5 neighbourhood
                    if shift_code.startswith("UNK_") or shift_code == "IGNORE":
                        from collections import Counter
                        votes = Counter()
                        for dx in range(-2, 3):
                            for dy in range(-2, 3):
                                sx = max(0, min(pix.width - 1, px + dx))
                                sy = max(0, min(pix.height - 1, py + dy))
                                sp = pix.pixel(sx, sy)
                                sc = classify_center_pixel(sp[0], sp[1], sp[2])
                                if sc not in ("IGNORE", "UNKNOWN") and not sc.startswith("UNK_"):
                                    votes[sc] += 1
                        if votes:
                            shift_code = votes.most_common(1)[0][0]

                if shift_code in ("IGNORE", "UNKNOWN"):
                    continue

                if shift_code.startswith("UNK_"):
                    unk_colors_found[shift_code] = unk_colors_found.get(shift_code, 0) + 1
                    continue

                # cs/sec employees don't work nights — reclassify AMNP → A1
                if emp_type in ("cs", "sec") and shift_code == "AMNP":
                    shift_code = "A1"

                cell_map[key] = shift_code

    doc.close()

    # ── Build output entries ─────────────────────────────────────────────
    entries = []
    for (emp_code, day), shift_code in sorted(cell_map.items()):
        d = date(year, month_num, day)
        entries.append({
            "hierarchy_code": emp_code,
            "date": d.isoformat(),
            "shift_code": shift_code,
        })

    if unk_colors_found:
        print(f"[EXTRACTOR DEBUG] Unknown colors found ({len(unk_colors_found)} distinct):")
        for unk, count in sorted(unk_colors_found.items(), key=lambda x: -x[1]):
            parts = unk.split("_")
            if len(parts) == 4:
                r, g, b = parts[1], parts[2], parts[3]
                print(f"  RGB({r},{g},{b}) ×{count} — add to KNOWN_COLORS if needed")

    return entries
