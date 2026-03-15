"""
Canonical employee registry for ASR Planification
==================================================

This module contains the single source of truth for employee data.
All extraction and solver modules import from here.
"""

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
