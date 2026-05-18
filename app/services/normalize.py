"""District name normalization for matching/dedup.

The same district appears as 'Brookhaven Public Schools', 'Brookhaven PS',
'Brookhaven Public Sch.' — we strip the common organizational suffixes
and punctuation so the matching layer sees just 'brookhaven'.

Pure function, unit-testable, no external state.
"""
from __future__ import annotations

import re

# Order matters: longer / more specific suffixes first so we strip them
# before shorter ones that may be substrings.
_SUFFIXES = [
    "consolidated school district",
    "community school district",
    "independent school district",
    "unified school district",
    "township public schools",
    "public school district",
    "county school district",
    "regional school district",
    "school district",
    "public schools",
    "public sch.",
    "public sch",
    "school dist",
    "co. schools",
    "county schools",
    "charter collective",
    "academy",
    "unified",
    "district",
    "schools",
    "school",
    "sch district",
    "sch.",
    "isd",
    "ssd",
    "csd",
    "sd",
    "ps",
]

# Strip "SD #74", "District 142", "Number 7" etc. — order matters
_NUMBER_PATTERNS = [
    re.compile(r"\bsd\s*#\s*\d+\b"),
    re.compile(r"\bdistrict\s+#?\s*\d+\b"),
    re.compile(r"\bno\.?\s*\d+\b"),
    re.compile(r"\bnumber\s*\d+\b"),
    re.compile(r"#\d+"),
]


def normalize_district_name(name: str) -> str:
    """Return a stable, lowercase, suffix-stripped form for matching.

    Examples:
        'Brookhaven Public Schools' -> 'brookhaven'
        'Brookhaven Public Sch.'    -> 'brookhaven'
        'Hartwell County Schools'   -> 'hartwell'
        'Salt Lake County SD'       -> 'salt lake'
        'SD #74 (Mission)'          -> 'mission'  (parens kept as primary if present)
        'Rio Verde ISD'             -> 'rio verde'
        'Maple Ridge School District 142' -> 'maple ridge'
    """
    if not name:
        return ""

    s = name.lower().strip()

    # Prefer parenthetical name when leading content is a code like "SD #74 (Mission)"
    paren_match = re.search(r"\(([^)]+)\)", s)
    if paren_match and re.search(r"^\s*(sd|district|isd)\s*[#\d\s]*\s*\(", s):
        s = paren_match.group(1).strip()
    else:
        # Otherwise just drop any trailing parens
        s = re.sub(r"\s*\([^)]*\)\s*", " ", s).strip()

    # Strip numbered designations
    for pat in _NUMBER_PATTERNS:
        s = pat.sub(" ", s)

    # Strip suffixes — iteratively, since stripping one may reveal another
    changed = True
    while changed:
        changed = False
        for suffix in _SUFFIXES:
            # word-boundary match at end of string
            pattern = re.compile(rf"\b{re.escape(suffix)}\s*$")
            if pattern.search(s):
                s = pattern.sub("", s).strip()
                changed = True

    # Collapse internal whitespace, strip punctuation
    s = re.sub(r"[^\w\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s
