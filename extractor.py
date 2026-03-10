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
# Last calibrated: 2026-03-09 from scan of 6 PDFs (Jan–Jun 2026)
KNOWN_COLORS = [
    # AMNP (Nuit) — dark blue / near-black
    (51,  54,  99,  "AMNP"),
    (4,   4,   14,  "AMNP"),
    (0,   5,   19,  "AMNP"),
    (0,   20,  34,  "AMNP"),
    (10,  10,  30,  "AMNP"),
    (0,   0,   10,  "AMNP"),
    (5,   5,   20,  "AMNP"),
    (71,  58,  140, "AMNP"),   # from Feb/Mar PDFs — purple-tint night
    # AMJP (Jour) — cyan / teal / bleu-vert
    (88,  157, 232, "AMJP"),
    (75,  157, 230, "AMJP"),
    (0,   82,  68,  "AMJP"),
    (0,   117, 105, "AMJP"),
    (0,   150, 200, "AMJP"),
    (20,  70,  60,  "AMJP"),
    (70,  226, 255, "AMJP"),   # bright cyan — 148 placements across PDFs
    (0,   149, 144, "AMJP"),   # dark teal variant
    # AMHS (Horaire S) — mauve / lilas / violet pâle
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
    # RS (Renfort Spécial) — orange vif
    (255, 127, 0,   "RS"),     # bright orange — 68 placements
    (255, 120, 0,   "RS"),
    # CMHN (Compensation nuit) — jaune vif
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
    # E (École) — olive / brun-vert
    (64,  90,  1,   "E"),
    (152, 157, 57,  "E"),
    (180, 192, 0,   "E"),
    (125, 139, 25,  "E"),
    (100, 120, 30,  "E"),
    (93,  221, 50,  "E"),      # bright green — école variant
    # FO9 (Formation) — violet / pourpre
    (160, 120, 200, "FO9"),
    (147, 112, 219, "FO9"),
    # M (Maladie) — rouge foncé
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
    # AP (Accident professionnel) — rose/saumon
    (255, 187, 172, "AP"),     # salmon pink — 6 placements
    (255, 180, 165, "AP"),
    # QC1 (Picchetto chef) — orange brun
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
    # C (Congé / Repos) — vert
    (0,   128, 0,   "C"),
    (0,   100, 0,   "C"),
    (30,  140, 30,  "C"),
    (154, 255, 182, "C"),      # light green — 6 placements
    (121, 255, 185, "C"),      # mint green — 1 placement
    (150, 255, 206, "C"),      # pale mint — 3 placements
    # Admin greys
    (210, 210, 210, "6FM"),    # Gris clair
    (195, 195, 195, "6FM"),
    (176, 196, 194, "6FM"),    # from PDF scan (blue-tint grey)
    (170, 194, 204, "6FM"),    # from PDF scan (blue-tint grey)
    (200, 255, 214, "6FM"),    # from PDF scan (light green-grey)
    (170, 170, 170, "6P1"),    # Gris moyen
    (155, 155, 155, "6P1"),
    (120, 120, 120, "A2"),     # Gris foncé
    (105, 105, 105, "A2"),
    (93,  107, 146, "A2"),     # from PDF scan (blue-tint grey)
    (70,  70,  70,  "A1"),     # Gris très foncé
    (55,  55,  55,  "A1"),
]

# Pre-sort threshold for quick white/background rejection
_COLOR_MATCH_THRESHOLD = 55  # Increased to 55 for broader matching

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
    doc = pymupdf.open(pdf_path)
    page = doc[0]
    
    dict_blocks = page.get_text("dict")["blocks"]
    text_blocks = [b for b in dict_blocks if b["type"] == 0]
    
    emp_rows = {}
    day_cols = {}
    
    # Base tolerances
    y_tol = 15
    x_tol = 18
    
    for b in text_blocks:
        for l in b["lines"]:
            for s in l["spans"]:
                txt = s["text"].strip()
                bbox = s["bbox"]
                cx = (bbox[0] + bbox[2]) / 2
                cy = (bbox[1] + bbox[3]) / 2
                
                # Identify employee codes "00" to "39"
                if len(txt) == 2 and txt.isdigit():
                    if 0 <= int(txt) <= 39:
                        emp_rows[txt] = {"y": cy, "bbox": bbox}
                
                # Identify day numbers "1" up to "31"
                if txt.isdigit():
                    val = int(txt)
                    if 1 <= val <= 31 and cy < 150:
                        if val not in day_cols:
                            day_cols[val] = {"x": cx, "bbox": bbox}
                        else:
                            if abs(cy - 120) < abs(day_cols[val]["y"] - 120 if "y" in day_cols[val] else 999):
                                day_cols[val] = {"x": cx, "y": cy, "bbox": bbox}

    img_cache = {}
    for item in page.get_images():
        xref = item[0]
        pix = pymupdf.Pixmap(doc, xref)
        if pix.n - pix.alpha < 3:
            pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
        
        w, h = pix.width, pix.height
        cx, cy = w // 2, h // 2
        try:
            pixel = pix.pixel(cx, cy)
            r, g, b = pixel[0], pixel[1], pixel[2]
            shift_code = classify_center_pixel(r, g, b)
            img_cache[xref] = {"code": shift_code, "rgb": (r, g, b)}
        except Exception:
            img_cache[xref] = {"code": "UNKNOWN", "rgb": (0,0,0)}

    cell_map = {}
    for xref in img_cache.keys():
        rects = page.get_image_rects(xref)
        for rect in rects:
            img_cy = (rect.y0 + rect.y1) / 2
            
            best_emp = None
            best_emp_dist = float('inf')
            for code, info in emp_rows.items():
                dist = abs(img_cy - info["y"])
                if dist < best_emp_dist:
                    best_emp_dist = dist
                    best_emp = code
            
            if best_emp and best_emp_dist < y_tol:
                original_shift_code = img_cache[xref]["code"]
                
                if original_shift_code == "IGNORE" or original_shift_code.startswith("UNK"):
                    continue
                    
                emp_type = EMPLOYEES[best_emp]["type"]
                if emp_type in ("cs", "sec") and original_shift_code == "AMNP":
                    original_shift_code = "A1"
                    
                # Support multi-day blocks by checking if the day's column center is within the rectangle's x boundaries
                for day, info in day_cols.items():
                    day_x = info["x"]
                    if (rect.x0 - 5) <= day_x <= (rect.x1 + 5):
                        shift_code = original_shift_code

                        key = (best_emp, day)
                        # We use distance 0 because if it's within bounds, it's an exact hit
                        if key not in cell_map:
                            cell_map[key] = {
                                "shift_code": shift_code,
                                "dist": best_emp_dist,
                            }
                    
    doc.close()
    
    entries = []
    # Import ALL recognized shift codes from the PDF so the dashboard
    # mirrors the PDF exactly. Previously only absence codes were imported,
    # causing a mismatch between PDF and dashboard display.
    ignored_prefixes = ("UNK_", "IGNORE", "UNKNOWN")

    # Collect unknown colors for debugging / calibration
    unk_colors_found = {}
    for (emp_code, day), info in sorted(cell_map.items()):
        code = info["shift_code"]
        if any(code.startswith(p) for p in ignored_prefixes):
            if code.startswith("UNK_"):
                unk_colors_found[code] = unk_colors_found.get(code, 0) + 1
            continue
        d = date(year, month_num, day)
        entries.append({
            "hierarchy_code": emp_code,
            "date": d.isoformat(),
            "shift_code": code
        })

    if unk_colors_found:
        print(f"[EXTRACTOR DEBUG] Unknown colors found ({len(unk_colors_found)} distinct):")
        for unk, count in sorted(unk_colors_found.items(), key=lambda x: -x[1]):
            parts = unk.split("_")
            if len(parts) == 4:
                r, g, b = parts[1], parts[2], parts[3]
                print(f"  RGB({r},{g},{b}) ×{count} — add to KNOWN_COLORS if needed")

    return entries
