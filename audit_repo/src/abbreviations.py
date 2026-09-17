"""
Billing abbreviation dictionary.

Invoice descriptions are compressed clinical shorthand; contract service names
are written out in full. Direct string similarity between the two fails badly
(mean score 0.37 on hospital_2, max 0.55) because "GI" shares no characters
with "Gastrointestinal" and "WD BD OCC" shares none with "Ward Bed Occupancy".

Expanding the shorthand first turns an unsolvable character-matching problem
into an ordinary token-overlap one.

The vocabulary was derived from the actual token frequencies in the line-item
files, not guessed: every key below appears in the data. Tokens that are
already full words (ROUTINE, CARDIAC, ...) need no entry — they pass through.
"""

from __future__ import annotations

import re

import pandas as pd

# Trailing supplier codes such as "/SA-8184" or "/NG-3022" carry no service
# meaning and only add noise to the match.
TRAILING_CODE = re.compile(r"/[A-Z]{2}-\d+\s*$")

ABBREVIATIONS: dict[str, str] = {
    # --- care setting / intensity qualifiers
    "ADV": "advanced",
    "AMB": "ambulatory",
    "ASST": "assisted",
    "BEDS": "bedside",
    "COMPR": "comprehensive",
    "CONT": "continuous",
    "ELECT": "elective",
    "EMER": "emergency",
    "EXT": "extended",
    "FOC": "focused",
    "INPT": "inpatient",
    "INTENS": "intensive",
    "INTERM": "intermittent",
    "OUTPT": "outpatient",
    "POSTOP": "postoperative",
    "PREOP": "preoperative",
    "RTN": "routine",
    "SPCLST": "specialist",
    "STD": "standard",
    "SUPV": "supervised",
    # --- clinical specialties
    "CARD": "cardiac",
    "DERM": "dermatologic",
    "ENT": "otolaryngologic",
    "GER": "geriatric",
    "GI": "gastrointestinal",
    "HAEM": "haematology",
    "HEP": "hepatic",
    "IMMUN": "immunologic",
    "INFECT": "infectious",
    "METAB": "metabolic",
    "MSK": "musculoskeletal",
    "NEURO": "neurological",
    "ONC": "oncology",
    "OBST": "obstetric",
    "OPHTH": "ophthalmic",
    "ORTHO": "orthopaedic",
    "PAED": "paediatric",
    "PALL": "palliative",
    "PSYCH": "psychiatric",
    "PULM": "pulmonary",
    "REN": "renal",
    "RHEUM": "rheumatologic",
    "UROL": "urologic",
    "VASC": "vascular",
    # --- service nouns
    "ADMIN": "administration",
    "ANAES": "anaesthesia",
    "ANLY": "analysis",
    "BIOP": "biopsy",
    "CONF": "conference",
    "CR": "care",
    "CRIT": "critical",
    "CS": "case",
    "DIAG": "diagnostic",
    "DIAL": "dialysis",
    "DISCH": "discharge",
    "DISP": "dispensing",
    "ENDO": "endoscopic",
    "ENDOSC": "endoscopic",
    "FRACT": "fraction",
    "HM": "home",
    "IMG": "imaging",
    "INF": "infusion",
    "INTERP": "interpretation",
    "ISOL": "isolation",
    "LAB": "laboratory",
    "MONIT": "monitoring",
    "NURS": "nursing",
    "NUTR": "nutritional",
    "OBS": "observation",
    "OCC": "occupancy",
    "PHARM": "pharmaceutical",
    "PHYSIO": "physiotherapy",
    "PLNG": "planning",
    "PNL": "panel",
    "PROC": "procedure",
    "PROG": "programme",
    "RADIOTHER": "radiotherapy",
    "RECOV": "recovery",
    "REHAB": "rehabilitation",
    "SESS": "session",
    "SPCM": "specimen",
    "STERIL": "sterilisation",
    "SUPP": "support",
    "SVC": "service",
    "TELEM": "telemetry",
    "THER": "therapy",
    "THTR": "theatre",
    "TM": "time",
    "TRANSF": "transfusion",
    "TRANSP": "transport",
    "VENT": "ventilation",
    "VST": "visit",
    # --- bed / room shorthand
    "BD": "bed",
    "RM": "room",
    "WD": "ward",
}


def expand(description: str) -> str:
    """Expand billing shorthand into contract vocabulary.

    Order matters: strip the supplier code first so it cannot contribute
    tokens, then expand token by token. Unknown tokens pass through unchanged
    rather than being dropped — losing a token we failed to recognise would
    silently weaken the match instead of merely not improving it.
    """
    text = TRAILING_CODE.sub("", description)
    text = re.sub(r"[-–/]", " ", text)
    tokens = [ABBREVIATIONS.get(token.upper(), token.lower()) for token in text.split()]
    return " ".join(tokens)


def expand_series(descriptions: pd.Series) -> pd.Series:
    """Vectorised-enough wrapper: expansion is pure string work on unique values."""
    unique = descriptions.drop_duplicates()
    mapping = {value: expand(value) for value in unique}
    return descriptions.map(mapping)
