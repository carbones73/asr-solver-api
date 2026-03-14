"""
Ambulance roster extractor
==========================

This module provides a collection of functions to parse monthly duty rosters for
ambulance services from PDF documents.  The plans we observed comprise a
matrix of crew members versus days of the month together with a colourful
legend of icons and coded duty types.  Extracting these tables reliably
requires a combination of PDF rendering, optical character recognition (OCR),
template matching for icons and some heuristic reasoning about the layout.

The core entry point is :func:`main` which takes a PDF file and produces
CSV/JSON exports of daily assignments.  The pipeline proceeds roughly as
follows:

1.  Each page of the PDF is rendered into a PIL image using PyMuPDF at a
    configurable DPI.  Rendering at a higher resolution (e.g. 200–300 DPI)
    helps later OCR and icon detection stages.

2.  The bottom portion of every page contains a legend explaining the
    meaning of each coloured icon and the time ranges associated with duty
    codes (e.g. ``AMJ1 | 23 C6 Jour 1 ; 0630-1840 ; 0630-1840``).  The
    :func:`extract_legend_from_page` function crops this region, detects
    candidate icon glyphs via contour analysis and reads the associated
    description using Tesseract.  It also builds a mapping from duty codes
    appearing in the legend to their respective start/finish times by
    performing a coarse OCR of the entire legend.

3.  A table of cells is recovered from the PDF using ``camelot`` in
    ``stream`` flavour.  Camelot yields the coordinates of each cell in PDF
    point space.  The :func:`extract_table_layout` helper converts those
    coordinates into pixel coordinates relative to the rendered image.  Each
    cell record stores its row/column indices and bounding box.

4.  Icons scattered throughout the duty matrix are located with
    :func:`detect_icons_in_page`.  We convert the page into HSV colour space,
    threshold for regions of high saturation (non white/grey areas) and
    extract connected components of an appropriate size.  For each detected
    patch, a perceptual hash (pHash) is computed and matched against the
    legend mapping.  Icon detections are later assigned to cells based upon
    bounding‑box overlap via :func:`associate_icons_to_cells`.

5.  Each cell is parsed for textual codes using OCR.  The helper
    :func:`parse_turni_e_nomi` returns any duty codes, vehicle identifiers,
    percentages or qualification abbreviations found in a cell.  When a
    recognised code exists in the legend mapping the corresponding
    start/finish times are looked up.  Otherwise the times are left blank.

6.  Records are assembled for every crew member on every day where an
    assignment exists.  Each record captures the date, operator name, role
    or qualification, time range, vehicle, list of icons present, their
    meanings, page number and a confidence estimate.  A detailed error log
    is also written to the output directory when the ``--debug`` flag is
    supplied.

The pipeline is necessarily heuristic.  Complex PDF layouts, fused cells,
scanned pages or novel icons may reduce accuracy.  Nevertheless the
modular design allows individual stages (legend parsing, icon detection,
table extraction) to be tuned or replaced independently.  See the README for
installation instructions and suggestions for further improvements.
"""

from __future__ import annotations

import argparse
import csv
import datetime as _dt
import json
import logging
import os
import re
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Dict, List, Tuple, Optional, Any

import camelot
import cv2
import fitz  # PyMuPDF
import imagehash
import numpy as np
import pandas as pd
import pytesseract
from PIL import Image


@dataclass
class LegendIcon:
    """Represents an entry in the legend.

    Attributes
    ----------
    hash: str
        The perceptual hash of the icon image.
    label: str
        Textual description of the icon extracted from the legend.
    bbox: Tuple[int, int, int, int]
        Bounding box of the icon in page pixel coordinates (left, top, right, bottom).
    """

    hash: str
    label: str
    bbox: Tuple[int, int, int, int]


@dataclass
class CellRecord:
    """Intermediate representation of a single cell in the duty matrix.

    Attributes
    ----------
    row: int
        Row index in the table (top row is 0).
    col: int
        Column index in the table (leftmost column is 0).
    bbox: Tuple[int, int, int, int]
        Pixel coordinates of the cell within the rendered page.
    text: str
        OCR text content of the cell (codes, name or other descriptors).
    codes: List[str]
        Recognised duty codes (e.g. ``['AMJ1', 'AMN2']``).
    vehicles: List[str]
        Recognised vehicle identifiers (e.g. ``['A6']``).
    percentage: Optional[str]
        Employment percentage if found (e.g. ``'100%'``).
    role: Optional[str]
        Qualification or role (e.g. ``'ad'``, ``'ta RS'``).
    icons: List[str]
        List of perceptual hashes of icons located in this cell.
    """

    row: int
    col: int
    bbox: Tuple[int, int, int, int]
    text: str = ""
    codes: List[str] = None
    vehicles: List[str] = None
    percentage: Optional[str] = None
    role: Optional[str] = None
    icons: List[str] = None

    def __post_init__(self) -> None:
        # Ensure lists are not shared between instances
        if self.codes is None:
            self.codes = []
        if self.vehicles is None:
            self.vehicles = []
        if self.icons is None:
            self.icons = []


def extract_pages_pdf(pdf_path: str, dpi: int = 200) -> List[Image.Image]:
    """Render all pages of a PDF into PIL images.

    Parameters
    ----------
    pdf_path : str
        Path to the PDF file.
    dpi : int, optional
        Dots per inch used for rendering.  Higher values improve OCR but slow down processing.

    Returns
    -------
    List[Image.Image]
        A list of PIL images corresponding to the pages of the PDF.
    """
    doc = fitz.open(pdf_path)
    pages: List[Image.Image] = []
    zoom = dpi / 72.0
    for page in doc:
        mat = fitz.Matrix(zoom, zoom)
        pix = page.get_pixmap(matrix=mat, alpha=False)
        img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        pages.append(img)
    return pages


def extract_legend_from_page(page_image: Image.Image, debug: bool = False) -> Tuple[Dict[str, LegendIcon], Dict[str, Tuple[str, str]]]:
    """Extract the legend mapping from a single page.

    This function crops the lower portion of the page containing the legend,
    detects coloured icons and reads their associated descriptions.  In
    parallel it performs a coarse OCR of the entire legend to discover duty
    codes and their start/end times.

    Parameters
    ----------
    page_image : PIL.Image
        The rendered page.
    debug : bool, optional
        If ``True``, intermediate debug images will be written into the current
        working directory.  Useful for tuning the icon detection heuristics.

    Returns
    -------
    Tuple[Dict[str, LegendIcon], Dict[str, Tuple[str, str]]]
        A mapping from perceptual hash to :class:`LegendIcon` describing
        the icon and a mapping from duty code to a `(start_time, end_time)` tuple.
    """
    width, height = page_image.size
    # Heuristically crop the bottom third of the page; the legend always resides there.
    legend_top = int(height * 0.65)
    legend_crop = page_image.crop((0, legend_top, width, height))

    # Convert to HSV and threshold for high saturation regions.  Icons are
    # colourful, whereas text and backgrounds are grey.
    np_img = np.array(legend_crop)
    hsv = cv2.cvtColor(np_img, cv2.COLOR_RGB2HSV)
    # saturation between 50 and 255; value above 50 ensures we ignore dark noise
    mask = cv2.inRange(hsv, (0, 50, 50), (180, 255, 255))
    # Morphological closing to merge adjacent pixels into blobs
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    cleaned = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel, iterations=1)
    cleaned = cv2.morphologyEx(cleaned, cv2.MORPH_CLOSE, kernel, iterations=1)
    # Find contours of candidate icons
    contours, _ = cv2.findContours(cleaned, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    legend_map: Dict[str, LegendIcon] = {}
    for cnt in contours:
        x, y, w, h = cv2.boundingRect(cnt)
        area = w * h
        # Discard very small or excessively large regions.  Icons are typically
        # around 10–40 px at 200 DPI; adjust thresholds as needed.
        if area < 80 or area > 4000:
            continue
        # Crop the icon image
        icon_img = legend_crop.crop((x, y, x + w, y + h))
        try:
            phash = str(imagehash.phash(icon_img))
        except Exception:
            continue
        # Extract associated label by taking a patch to the right of the icon.
        # We allow up to 250 px to capture the full description.
        label_x1 = min(x + w + 2, legend_crop.width)
        label_x2 = min(x + w + 250, legend_crop.width)
        label_y1 = max(y - 5, 0)
        label_y2 = min(y + h + 5, legend_crop.height)
        label_region = legend_crop.crop((label_x1, label_y1, label_x2, label_y2))
        raw_text = pytesseract.image_to_string(label_region, lang='fra+eng', config='--psm 6')
        text = ' '.join(raw_text.split())
        if not text:
            continue
        # Convert bounding box to absolute page coordinates
        abs_bbox = (x, legend_top + y, x + w, legend_top + y + h)
        existing = legend_map.get(phash)
        if existing:
            # Keep the longer label (in case of duplicates with truncated text)
            if len(text) > len(existing.label):
                legend_map[phash] = LegendIcon(hash=phash, label=text, bbox=abs_bbox)
        else:
            legend_map[phash] = LegendIcon(hash=phash, label=text, bbox=abs_bbox)
        if debug:
            # Optionally save debug crops
            debug_dir = Path('debug_legend')
            debug_dir.mkdir(exist_ok=True)
            icon_img.save(debug_dir / f'{phash[:8]}_icon.png')
            label_region.save(debug_dir / f'{phash[:8]}_label.png')

    # Build a mapping of duty codes to time intervals from the OCR of the legend.
    schedule_map: Dict[str, Tuple[str, str]] = {}
    legend_text = pytesseract.image_to_string(legend_crop, lang='fra+eng', config='--psm 6')
    for line in legend_text.split('\n'):
        line = line.strip()
        if not line:
            continue
        # Replace separators to simplify splitting
        tmp = line.replace('|', ' ').replace('•', ' ')
        # Detect duty codes (letters followed by optional digits)
        codes = re.findall(r'[A-Z]{1,4}\d{0,2}', tmp)
        # Detect time patterns (e.g. 06:30, 0630 or 06h30)
        times = re.findall(r'(\d{1,2}[h:]?\d{2})', tmp)
        if codes and len(times) >= 2:
            # Normalise times to HH:MM by inserting ':' where necessary
            def norm(t: str) -> str:
                t = t.replace('h', ':')
                if len(t) == 4:
                    return t[:2] + ':' + t[2:]
                if len(t) == 3:
                    return '0' + t[0] + ':' + t[1:]
                return t
            code = codes[0]
            start = norm(times[0])
            end = norm(times[1])
            schedule_map[code] = (start, end)
    return legend_map, schedule_map


def recognize_and_normalize_icon(icon_image: Image.Image) -> str:
    """Compute a perceptual hash for an icon.

    Parameters
    ----------
    icon_image : PIL.Image
        The icon image to hash.

    Returns
    -------
    str
        Hexadecimal representation of the pHash.
    """
    try:
        phash = imagehash.phash(icon_image.resize((64, 64)))
        return str(phash)
    except Exception:
        # In case hashing fails, fall back to an empty string
        return ''


def extract_table_layout(pdf_path: str, page_index: int, page_image: Image.Image) -> Dict[str, Any]:
    """Extract a table layout using Camelot and convert cell coordinates to pixel space.

    Camelot reads tables directly from the PDF and yields a two‑dimensional list
    of cells with x/y coordinates in PDF points (origin bottom-left).  This
    helper translates those coordinates into pixel space relative to the
    rendered page image, preserving row and column indices.

    Parameters
    ----------
    pdf_path : str
        Path to the PDF file.
    page_index : int
        Zero‑based page number.
    page_image : PIL.Image
        Rendered page image from :func:`extract_pages_pdf` used to compute the
        scaling factors.

    Returns
    -------
    Dict[str, Any]
        A dictionary with keys ``cells`` (list of :class:`CellRecord`),
        ``df`` (pandas DataFrame of raw text extracted by Camelot) and
        ``n_rows``/``n_cols`` summarising the table dimensions.
    """
    # Use Camelot to parse the page; 'stream' flavour is robust to tables without
    # explicit ruling lines.  We read only the required page.
    tables = camelot.read_pdf(pdf_path, pages=str(page_index + 1), flavor='stream')
    if not tables:
        raise ValueError(f"No tables found on page {page_index+1} of {pdf_path}")
    table = tables[0]
    pdf_width, pdf_height = table.pdf_size
    img_width, img_height = page_image.size
    scale_x = img_width / pdf_width
    scale_y = img_height / pdf_height
    records: List[CellRecord] = []
    for row_idx, row_cells in enumerate(table.cells):
        for col_idx, cell in enumerate(row_cells):
            # Convert PDF coords to pixel coords (origin top‑left)
            x1 = int(cell.x1 * scale_x)
            x2 = int(cell.x2 * scale_x)
            # PDF origin is bottom-left; invert Y axis
            y1 = int((pdf_height - cell.y2) * scale_y)
            y2 = int((pdf_height - cell.y1) * scale_y)
            # Ensure boundaries are within image
            x1 = max(0, min(img_width - 1, x1))
            x2 = max(0, min(img_width - 1, x2))
            y1 = max(0, min(img_height - 1, y1))
            y2 = max(0, min(img_height - 1, y2))
            bbox = (x1, y1, x2, y2)
            record = CellRecord(row=row_idx, col=col_idx, bbox=bbox)
            records.append(record)
    return {
        'cells': records,
        'df': table.df,
        'n_rows': len(table.cells),
        'n_cols': len(table.cells[0]) if table.cells else 0
    }


def detect_icons_in_page(page_image: Image.Image) -> List[Dict[str, Any]]:
    """Detect coloured icons on a page.

    This function identifies regions of high saturation (colourful) within the
    page, filters by size and computes a perceptual hash for each.  It does
    not attempt to interpret the meaning of the hash—that is performed later
    by matching against the legend mapping.

    Parameters
    ----------
    page_image : PIL.Image
        Rendered page image.

    Returns
    -------
    List[Dict[str, Any]]
        A list of dictionaries with keys ``bbox`` (pixel coordinates) and
        ``hash`` (pHash string).
    """
    np_img = np.array(page_image)
    hsv = cv2.cvtColor(np_img, cv2.COLOR_RGB2HSV)
    # Threshold for regions that are sufficiently coloured.  Empirically we
    # require a moderately high saturation and brightness to avoid grey text
    # regions.  Adjust these bounds if you encounter false positives.
    mask = cv2.inRange(hsv, (0, 70, 70), (180, 255, 255))
    # Morphological operations to clean up noise
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel, iterations=1)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=1)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    detections: List[Dict[str, Any]] = []
    for cnt in contours:
        x, y, w, h = cv2.boundingRect(cnt)
        area = w * h
        # Exclude tiny blobs (<20px²) and very large ones (>5000px²)
        if area < 50 or area > 8000:
            continue
        # Extract icon image
        icon = page_image.crop((x, y, x + w, y + h))
        phash = recognize_and_normalize_icon(icon)
        if phash:
            detections.append({'bbox': (x, y, x + w, y + h), 'hash': phash})
    return detections


def associate_icons_to_cells(cells: List[CellRecord], icons: List[Dict[str, Any]]) -> None:
    """Assign detected icons to their containing cells.

    Each detected icon is associated to the cell whose bounding box encloses
    the centre of the icon.  If an icon overlaps multiple cells, it is
    assigned to the one with the largest overlap area.

    Parameters
    ----------
    cells : List[CellRecord]
        Flattened list of table cells.
    icons : List[Dict[str, Any]]
        Detected icons with ``bbox`` and ``hash``.
    """
    for icon in icons:
        ix1, iy1, ix2, iy2 = icon['bbox']
        cx = (ix1 + ix2) // 2
        cy = (iy1 + iy2) // 2
        best_cell: Optional[CellRecord] = None
        best_area = 0
        for cell in cells:
            x1, y1, x2, y2 = cell.bbox
            if x1 <= cx <= x2 and y1 <= cy <= y2:
                # compute overlap area
                inter_x1 = max(x1, ix1)
                inter_y1 = max(y1, iy1)
                inter_x2 = min(x2, ix2)
                inter_y2 = min(y2, iy2)
                overlap = max(0, inter_x2 - inter_x1) * max(0, inter_y2 - inter_y1)
                if overlap > best_area:
                    best_area = overlap
                    best_cell = cell
        if best_cell:
            best_cell.icons.append(icon['hash'])


def parse_turni_e_nomi(cell_img: Image.Image) -> Dict[str, Any]:
    """Extract textual information from a cell image.

    Using Tesseract OCR, this helper attempts to recognise duty codes,
    vehicle identifiers, percentages and role abbreviations.  Names are
    typically extracted separately from the first column; this function
    focuses on codes present in duty cells.

    Parameters
    ----------
    cell_img : PIL.Image
        The cropped cell image.

    Returns
    -------
    Dict[str, Any]
        Dictionary containing keys ``text``, ``codes``, ``vehicles``,
        ``percentage`` and ``role``.
    """
    # Convert to grayscale and upscale slightly to improve OCR accuracy
    img = cell_img.convert('L')
    w, h = img.size
    # Upscale small images to at least 64 pixels wide
    scale_factor = 2 if w < 64 else 1
    if scale_factor != 1:
        img = img.resize((w * scale_factor, h * scale_factor), Image.BILINEAR)
    # Use Tesseract with a single block of text (PSM 7) for small regions
    raw_text = pytesseract.image_to_string(img, lang='fra+eng', config='--psm 7')
    text = ' '.join(raw_text.split())
    result: Dict[str, Any] = {
        'text': text,
        'codes': [],
        'vehicles': [],
        'percentage': None,
        'role': None,
    }
    if not text:
        return result
    # Match duty codes (e.g. AMJ1, AMN2)
    result['codes'] = re.findall(r'[A-Z]{1,4}\d{0,2}', text)
    # Vehicle identifiers typically start with 'A' followed by digits
    result['vehicles'] = re.findall(r'A\d+', text)
    # Employment percentage like 80%, 100%
    perc_match = re.search(r'(\d{1,3}%+)', text)
    if perc_match:
        result['percentage'] = perc_match.group(1)
    # Role abbreviations: ad, ta, sec, RS, etc.
    role_match = re.search(r'\b(ad|ta|sec|RS)\b', text, flags=re.IGNORECASE)
    if role_match:
        result['role'] = role_match.group(1).lower()
    return result


def build_assignments_for_page(
    pdf_path: str,
    page_idx: int,
    page_image: Image.Image,
    legend_map: Dict[str, LegendIcon],
    schedule_map: Dict[str, Tuple[str, str]],
    debug: bool = False,
    year_hint: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Assemble daily assignment records for a single page.

    Parameters
    ----------
    pdf_path : str
        Path to the original PDF.
    page_idx : int
        Zero‑based page number.
    page_image : PIL.Image
        Rendered page image.
    legend_map : Dict[str, LegendIcon]
        Mapping of pHash to legend descriptions.
    schedule_map : Dict[str, Tuple[str, str]]
        Mapping from duty codes to time ranges.
    debug : bool, optional
        If True, additional diagnostic information will be logged.
    year_hint : int, optional
        Year extracted from the filename or provided externally.  If absent
        the current year will be used.

    Returns
    -------
    List[Dict[str, Any]]
        A list of assignment dictionaries ready for export.
    """
    table_data = extract_table_layout(pdf_path, page_idx, page_image)
    cells: List[CellRecord] = table_data['cells']
    df: pd.DataFrame = table_data['df']
    n_rows, n_cols = table_data['n_rows'], table_data['n_cols']

    # Determine which row contains day numbers.  We look for a row with at
    # least five entries matching the pattern of a day (e.g. '12.').
    date_row_idx: Optional[int] = None
    for i in range(min(n_rows, 10)):
        row = df.iloc[i]
        count = 0
        for val in row:
            if isinstance(val, str) and re.match(r'^\d+\.$', val.strip()):
                count += 1
        if count >= 5:
            date_row_idx = i
            break
    if date_row_idx is None:
        raise RuntimeError("Failed to locate date row in the table header.")
    # Identify the starting column of day columns by finding the first numeric
    # entry in the date row.
    start_col_idx: Optional[int] = None
    num_days = 0
    date_values: List[int] = []
    for col_idx, val in enumerate(df.iloc[date_row_idx]):
        if isinstance(val, str) and re.match(r'^\d+\.$', val.strip()):
            if start_col_idx is None:
                start_col_idx = col_idx
            date_values.append(int(val.strip().strip('.')))
    num_days = len(date_values)
    if start_col_idx is None or num_days == 0:
        raise RuntimeError("Unable to determine the start column for dates.")
    # Attempt to deduce the month from the row above the day names.  We look
    # for a three‑letter month abbreviation (jan, fév, mar, etc.).
    month_idx = date_row_idx - 2 if date_row_idx >= 2 else 0
    month_row = df.iloc[month_idx]
    month_name = ''
    for col in range(start_col_idx, start_col_idx + min(num_days, 3)):
        val = month_row[col]
        if isinstance(val, str) and len(val.strip()) >= 3:
            month_name = val.strip().lower()[:3]
            break
    month_map = {
        'jan': 1, 'fév': 2, 'fev': 2, 'mar': 3, 'avr': 4, 'mai': 5,
        'jun': 6, 'jul': 7, 'aoû': 8, 'aou': 8, 'sep': 9, 'oct': 10,
        'nov': 11, 'déc': 12, 'dec': 12
    }
    month_num = month_map.get(month_name, None)
    if month_num is None:
        # Fallback: try to parse from file name (e.g. janvier2026)
        base = Path(pdf_path).stem.lower()
        for k, v in month_map.items():
            if k in base:
                month_num = v
                break
    if month_num is None:
        month_num = _dt.date.today().month
    # Determine the year: either from the hint or extracted from the filename
    year = year_hint or _dt.date.today().year
    # Extract year digits from filename if available
    if year_hint is None:
        base_digits = re.findall(r'(19\d{2}|20\d{2})', Path(pdf_path).stem)
        if base_digits:
            year = int(base_digits[0])
    # Build date strings for each day column
    date_strings: List[str] = []
    for day in date_values:
        try:
            date_obj = _dt.date(year, month_num, day)
        except Exception:
            # In case of invalid date (e.g. February 30), skip
            continue
        date_strings.append(date_obj.isoformat())
    # Detect icons on the page once per page
    icon_detections = detect_icons_in_page(page_image)
    # Assign icons to cells
    associate_icons_to_cells(cells, icon_detections)
    # Build assignments
    assignments: List[Dict[str, Any]] = []
    current_name = None
    current_percentage = None
    current_role = None
    # Iterate through table rows starting after the header block
    for row_idx in range(date_row_idx + 1, n_rows):
        row = df.iloc[row_idx]
        # Determine if this row introduces a new crew member.  A non‑empty
        # string in the first column indicates a new name.
        name_cell = row.iloc[0]
        if isinstance(name_cell, str) and name_cell.strip():
            current_name = name_cell.strip()
            # Reset meta fields
            current_percentage = None
            current_role = None
        # Extract meta information from columns before the date range
        meta_values = []
        for col in range(1, start_col_idx):
            val = row.iloc[col]
            if isinstance(val, str) and val.strip():
                meta_values.append(val.strip())
        # If meta_values include a percentage or role, update
        for meta in meta_values:
            if re.match(r'^\d{1,3}%$', meta):
                current_percentage = meta
            elif re.match(r'^[a-zA-Z].*', meta):
                current_role = meta
        # Skip processing if no current_name (this line may be part of a header)
        if not current_name:
            continue
        # Process each day cell
        for day_offset, day_str in enumerate(date_strings):
            col_idx = start_col_idx + day_offset
            if col_idx >= n_cols:
                continue
            # Find the corresponding CellRecord
            cell_record = next((c for c in cells if c.row == row_idx and c.col == col_idx), None)
            if cell_record is None:
                continue
            x1, y1, x2, y2 = cell_record.bbox
            # Crop the cell region
            cell_img = page_image.crop((x1, y1, x2, y2))
            parsed = parse_turni_e_nomi(cell_img)
            # Determine whether this cell contains any assignments (codes or icons)
            if not parsed['codes'] and not cell_record.icons:
                continue
            # Build record
            meaning_list: List[str] = []
            for h in cell_record.icons:
                icon_info = legend_map.get(h)
                if icon_info:
                    meaning_list.append(icon_info.label)
            # Map codes to times if available
            start_time: Optional[str] = None
            end_time: Optional[str] = None
            if parsed['codes']:
                for code in parsed['codes']:
                    if code in schedule_map:
                        st, et = schedule_map[code]
                        # Use the first matched code
                        start_time, end_time = st, et
                        break
            assignment = {
                'data': day_str,
                'nome_operatore': current_name,
                'ruolo_qualifica': current_role,
                'turno_inizio': start_time,
                'turno_fine': end_time,
                'veicolo_mezzo': parsed['vehicles'][0] if parsed['vehicles'] else None,
                'icone_presenti': cell_record.icons,
                'significato_icone': meaning_list,
                'pagina': page_idx + 1,
                'bbox': cell_record.bbox,
                'confidence': 1.0  # Placeholder confidence; could be refined
            }
            assignments.append(assignment)
            if debug:
                logging.debug(f"Assignment added: {assignment}")
    return assignments


def export_results(records: List[Dict[str, Any]], out_dir: str, pdf_name: str) -> None:
    """Export the extracted assignments to CSV and JSON files.

    Parameters
    ----------
    records : List[Dict[str, Any]]
        Assignment dictionaries produced by :func:`build_assignments_for_page`.
    out_dir : str
        Directory where the output files will be written.
    pdf_name : str
        Base name of the source PDF used to construct the output filenames.
    """
    if not records:
        logging.warning(f"No assignments extracted from {pdf_name}")
        return
    df = pd.DataFrame(records)
    out_path_csv = os.path.join(out_dir, f"{pdf_name}.csv")
    out_path_json = os.path.join(out_dir, f"{pdf_name}.json")
    df.to_csv(out_path_csv, index=False)
    df.to_json(out_path_json, orient='records', indent=2, force_ascii=False)
    logging.info(f"Written {len(records)} assignments to {out_path_csv} and {out_path_json}")


def main() -> None:
    """Command‑line interface for the extractor.

    Use ``python ambulance_extractor.py --pdf path/to/file.pdf --out output_directory``.
    Multiple PDF paths can be provided by repeating the ``--pdf`` option.  When
    the ``--debug`` flag is set, debug level logging is enabled and
    intermediate artefacts (such as cropped legends) may be produced.
    """
    parser = argparse.ArgumentParser(description="Extract duty assignments from ambulance rosters")
    parser.add_argument('--pdf', action='append', required=True, help="Path to a PDF file to process. Repeat for multiple files.")
    parser.add_argument('--out', required=True, help="Output directory for CSV and JSON files.")
    parser.add_argument('--dpi', type=int, default=200, help="Rendering DPI for PDF pages (default 200).")
    parser.add_argument('--debug', action='store_true', help="Enable debug logging and produce extra diagnostics.")
    args = parser.parse_args()
    # Configure logging
    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO, format='%(asctime)s %(levelname)s: %(message)s')
    out_dir = args.out
    os.makedirs(out_dir, exist_ok=True)
    for pdf_path in args.pdf:
        pdf_path = os.path.abspath(pdf_path)
        pdf_name = Path(pdf_path).stem
        logging.info(f"Processing {pdf_path}")
        pages = extract_pages_pdf(pdf_path, dpi=args.dpi)
        all_records: List[Dict[str, Any]] = []
        for page_idx, page_img in enumerate(pages):
            logging.info(f"  Page {page_idx + 1} of {len(pages)}")
            # Extract legend and schedule mapping once per page
            legend_map, schedule_map = extract_legend_from_page(page_img, debug=args.debug)
            page_records = build_assignments_for_page(
                pdf_path=pdf_path,
                page_idx=page_idx,
                page_image=page_img,
                legend_map=legend_map,
                schedule_map=schedule_map,
                debug=args.debug,
            )
            all_records.extend(page_records)
        export_results(all_records, out_dir, pdf_name)


if __name__ == '__main__':
    main()