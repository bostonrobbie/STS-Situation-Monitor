"""Lightweight entity extraction from observation text.

Extracts people, organizations, locations, dates, and numeric quantities
from claim text using pattern matching and gazetteers. No external NLP
library required — designed for speed and offline operation.

For higher accuracy, can optionally use the local LLM for extraction.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime


@dataclass(slots=True)
class ExtractedEntity:
    text: str
    entity_type: str  # person, organization, location, date, quantity, weapon, event
    confidence: float = 0.5
    start: int = 0
    end: int = 0
    normalized: str = ""


# ── Gazetteers ──────────────────────────────────────────────────────────

_WORLD_LEADERS: set[str] = {
    "Biden", "Trump", "Putin", "Zelensky", "Xi Jinping", "Macron", "Scholz",
    "Sunak", "Starmer", "Modi", "Erdogan", "Netanyahu", "Lula", "Milei",
    "Kishida", "Ishiba", "Marcos", "Sisi", "MBS", "Kim Jong Un",
    "Khamenei", "Assad", "Maduro", "Ramaphosa", "Tinubu",
}

_MAJOR_ORGS: set[str] = {
    "NATO", "UN", "EU", "WHO", "IAEA", "ICC", "ICJ", "OSCE", "ASEAN",
    "BRICS", "G7", "G20", "OPEC", "IMF", "World Bank", "WTO",
    "Red Cross", "ICRC", "MSF", "Amnesty International", "Human Rights Watch",
    "Wagner", "Hezbollah", "Hamas", "Houthis", "ISIS", "ISIL", "Al-Qaeda",
    "Taliban", "PKK", "IRA", "FARC", "Boko Haram", "Al-Shabaab",
    "CIA", "FBI", "NSA", "MI6", "FSB", "GRU", "Mossad", "BND",
    "Pentagon", "Kremlin", "White House", "Downing Street", "Élysée",
    "Reuters", "AP", "AFP", "BBC", "CNN", "Al Jazeera", "TASS",
    "SpaceX", "Lockheed Martin", "Raytheon", "BAE Systems", "Boeing",
}

_CONFLICT_LOCATIONS: set[str] = {
    "Ukraine", "Gaza", "West Bank", "Crimea", "Donbas", "Donetsk", "Luhansk",
    "Kherson", "Zaporizhzhia", "Mariupol", "Bakhmut", "Avdiivka",
    "Rafah", "Khan Younis", "Jabalia", "Nablus", "Jenin", "Ramallah",
    "Syria", "Aleppo", "Idlib", "Damascus", "Homs",
    "Yemen", "Sanaa", "Aden", "Hodeidah", "Marib",
    "Sudan", "Khartoum", "Darfur", "El Fasher",
    "Myanmar", "Rakhine", "Shan", "Kachin",
    "Taiwan", "South China Sea", "Strait of Taiwan",
    "Somalia", "Mogadishu", "Ethiopia", "Tigray",
    "Libya", "Tripoli", "Benghazi", "Niger", "Mali", "Burkina Faso",
    "Lebanon", "Beirut", "Iran", "Tehran", "Iraq", "Baghdad", "Mosul",
    "Afghanistan", "Kabul", "Pakistan", "Kashmir",
    "North Korea", "Pyongyang", "Korea",
}

_COUNTRIES: set[str] = {
    "Afghanistan", "Albania", "Algeria", "Argentina", "Armenia", "Australia",
    "Austria", "Azerbaijan", "Bahrain", "Bangladesh", "Belarus", "Belgium",
    "Bolivia", "Bosnia", "Brazil", "Bulgaria", "Cambodia", "Cameroon",
    "Canada", "Chad", "Chile", "China", "Colombia", "Congo", "Croatia",
    "Cuba", "Cyprus", "Czechia", "Denmark", "Ecuador", "Egypt", "Estonia",
    "Ethiopia", "Finland", "France", "Georgia", "Germany", "Ghana", "Greece",
    "Guatemala", "Haiti", "Honduras", "Hungary", "Iceland", "India",
    "Indonesia", "Iran", "Iraq", "Ireland", "Israel", "Italy", "Japan",
    "Jordan", "Kazakhstan", "Kenya", "Kuwait", "Kyrgyzstan", "Latvia",
    "Lebanon", "Libya", "Lithuania", "Malaysia", "Mali", "Mexico",
    "Moldova", "Mongolia", "Morocco", "Mozambique", "Myanmar", "Nepal",
    "Netherlands", "New Zealand", "Nicaragua", "Niger", "Nigeria", "Norway",
    "Oman", "Pakistan", "Palestine", "Panama", "Peru", "Philippines",
    "Poland", "Portugal", "Qatar", "Romania", "Russia", "Rwanda",
    "Saudi Arabia", "Senegal", "Serbia", "Singapore", "Slovakia", "Slovenia",
    "Somalia", "South Africa", "South Korea", "Spain", "Sri Lanka", "Sudan",
    "Sweden", "Switzerland", "Syria", "Taiwan", "Tanzania", "Thailand",
    "Tunisia", "Turkey", "Turkmenistan", "UAE", "Uganda", "Ukraine",
    "United Kingdom", "United States", "Uruguay", "Uzbekistan", "Venezuela",
    "Vietnam", "Yemen", "Zambia", "Zimbabwe",
}

_WEAPON_TERMS: set[str] = {
    "missile", "rocket", "drone", "UAV", "ICBM", "SLBM", "cruise missile",
    "ballistic missile", "hypersonic", "nuclear", "chemical weapon",
    "biological weapon", "IED", "HIMARS", "Patriot", "S-300", "S-400",
    "ATACMS", "Storm Shadow", "SCALP", "Javelin", "NLAW", "Leopard",
    "Abrams", "F-16", "F-35", "Su-35", "MiG", "artillery", "mortar",
    "cluster munition", "thermobaric", "depleted uranium",
}

# ── Patterns ────────────────────────────────────────────────────────────

_CAPITALIZED_PHRASE = re.compile(
    r"\b([A-Z][a-z]+(?:\s+(?:al-|bin\s+|von\s+|de\s+|van\s+)?[A-Z][a-z]+){1,4})\b"
)

_DATE_PATTERNS = [
    re.compile(r"\b(\d{1,2}\s+(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{4})\b", re.I),
    re.compile(r"\b((?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},?\s+\d{4})\b", re.I),
    re.compile(r"\b(\d{4}-\d{2}-\d{2})\b"),
]

_QUANTITY_PATTERN = re.compile(
    r"\b(\d{1,3}(?:,\d{3})*(?:\.\d+)?)\s+"
    r"(people|killed|dead|wounded|injured|displaced|refugees|troops|soldiers|casualties|civilians|hostages|prisoners|migrants|protesters|arrests)\b",
    re.I,
)

_COORD_PATTERN = re.compile(
    r"(-?\d{1,3}\.\d{2,6})\s*[,/]\s*(-?\d{1,3}\.\d{2,6})"
)


def extract_entities(text: str) -> list[ExtractedEntity]:
    """Extract entities from a text string using patterns and gazetteers."""
    entities: list[ExtractedEntity] = []
    seen: set[str] = set()

    def _add(ent_text: str, ent_type: str, confidence: float, start: int = 0, end: int = 0) -> None:
        key = f"{ent_type}:{ent_text.lower()}"
        if key not in seen:
            seen.add(key)
            entities.append(ExtractedEntity(
                text=ent_text, entity_type=ent_type,
                confidence=confidence, start=start, end=end,
                normalized=ent_text.strip(),
            ))

    # Gazetteer matches (high confidence)
    for leader in _WORLD_LEADERS:
        if leader in text:
            idx = text.index(leader)
            _add(leader, "person", 0.95, idx, idx + len(leader))

    for org in _MAJOR_ORGS:
        if org in text:
            idx = text.index(org)
            _add(org, "organization", 0.92, idx, idx + len(org))

    for loc in _CONFLICT_LOCATIONS:
        if loc in text:
            idx = text.index(loc)
            _add(loc, "location", 0.90, idx, idx + len(loc))

    for country in _COUNTRIES:
        if country in text:
            idx = text.index(country)
            _add(country, "location", 0.85, idx, idx + len(country))

    for weapon in _WEAPON_TERMS:
        pattern = re.compile(r"\b" + re.escape(weapon) + r"\b", re.I)
        m = pattern.search(text)
        if m:
            _add(m.group(0), "weapon", 0.80, m.start(), m.end())

    # Capitalized phrase detection (potential person/org names not in gazetteer)
    for m in _CAPITALIZED_PHRASE.finditer(text):
        phrase = m.group(1)
        # Skip if already found via gazetteer
        key_person = f"person:{phrase.lower()}"
        key_org = f"organization:{phrase.lower()}"
        key_loc = f"location:{phrase.lower()}"
        if key_person in seen or key_org in seen or key_loc in seen:
            continue
        # Heuristic: 2-word phrases are likely person names, 3+ could be org/location
        words = phrase.split()
        if len(words) == 2:
            _add(phrase, "person", 0.45, m.start(), m.end())
        elif len(words) >= 3:
            _add(phrase, "organization", 0.35, m.start(), m.end())

    # Date extraction
    for pat in _DATE_PATTERNS:
        for m in pat.finditer(text):
            _add(m.group(1), "date", 0.90, m.start(), m.end())

    # Quantity extraction (casualties, displacement, etc.)
    for m in _QUANTITY_PATTERN.finditer(text):
        full = m.group(0)
        _add(full, "quantity", 0.75, m.start(), m.end())

    # Coordinate extraction
    for m in _COORD_PATTERN.finditer(text):
        coord_text = f"{m.group(1)}, {m.group(2)}"
        _add(coord_text, "location", 0.80, m.start(), m.end())

    return entities


def extract_entities_batch(texts: list[str]) -> dict[int, list[ExtractedEntity]]:
    """Extract entities from multiple texts, keyed by index."""
    return {i: extract_entities(t) for i, t in enumerate(texts)}


# ── LLM-assisted extraction ────────────────────────────────────────────

LLM_ENTITY_PROMPT = """Extract all named entities from the following text.
Return a JSON array where each element has:
- "text": the entity as it appears
- "type": one of "person", "organization", "location", "date", "quantity", "weapon", "event"
- "confidence": 0.0 to 1.0

Text:
{text}

Return ONLY the JSON array, no explanation."""


def build_llm_entity_prompt(text: str) -> str:
    """Build the LLM prompt for entity extraction."""
    return LLM_ENTITY_PROMPT.format(text=text[:3000])
