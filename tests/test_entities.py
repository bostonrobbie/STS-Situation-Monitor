"""Tests for the entity extraction module."""
from __future__ import annotations

import pytest

from sts_monitor.entities import ExtractedEntity, extract_entities, extract_entities_batch

pytestmark = pytest.mark.unit


# ── Person extraction ──────────────────────────────────────────────────


def test_extract_world_leader_by_name() -> None:
    entities = extract_entities("Putin ordered new mobilization in Russia")
    persons = [e for e in entities if e.entity_type == "person"]
    assert any(e.text == "Putin" for e in persons)


def test_extract_multi_word_leader_name() -> None:
    entities = extract_entities("Xi Jinping met with delegates at the summit")
    persons = [e for e in entities if e.entity_type == "person"]
    assert any(e.text == "Xi Jinping" for e in persons)


def test_extract_multiple_leaders() -> None:
    entities = extract_entities("Biden and Macron discussed Ukraine sanctions with Zelensky")
    person_names = {e.text for e in entities if e.entity_type == "person"}
    assert {"Biden", "Macron", "Zelensky"} <= person_names


def test_person_confidence_from_gazetteer() -> None:
    entities = extract_entities("Netanyahu addressed parliament")
    person = next(e for e in entities if e.text == "Netanyahu")
    assert person.confidence == 0.95


def test_gazetteered_person_detected_in_context() -> None:
    entities = extract_entities("President Biden confirmed the report on Ukraine")
    persons = [e for e in entities if e.entity_type == "person"]
    assert any("Biden" in e.text for e in persons)


# ── Organization extraction ────────────────────────────────────────────


def test_extract_organization() -> None:
    entities = extract_entities("NATO announced new deployments")
    orgs = [e for e in entities if e.entity_type == "organization"]
    assert any(e.text == "NATO" for e in orgs)


def test_extract_multi_word_organization() -> None:
    entities = extract_entities("The Red Cross deployed relief teams")
    orgs = [e for e in entities if e.entity_type == "organization"]
    assert any(e.text == "Red Cross" for e in orgs)


def test_organization_confidence() -> None:
    entities = extract_entities("WHO declared a health emergency")
    org = next(e for e in entities if e.text == "WHO")
    assert org.confidence == 0.92


def test_extract_multiple_organizations() -> None:
    entities = extract_entities("Both NATO and the EU condemned the attacks")
    org_names = {e.text for e in entities if e.entity_type == "organization"}
    assert {"NATO", "EU"} <= org_names


# ── Location extraction ────────────────────────────────────────────────


def test_extract_conflict_location() -> None:
    entities = extract_entities("Heavy fighting reported in Bakhmut")
    locations = [e for e in entities if e.entity_type == "location"]
    assert any(e.text == "Bakhmut" for e in locations)


def test_extract_country() -> None:
    entities = extract_entities("Flooding caused massive damage in Bangladesh")
    locations = [e for e in entities if e.entity_type == "location"]
    assert any(e.text == "Bangladesh" for e in locations)


def test_conflict_location_higher_confidence_than_country() -> None:
    # Gaza is in _CONFLICT_LOCATIONS (0.90), not just _COUNTRIES
    entities = extract_entities("Aid convoy entered Gaza")
    loc = next(e for e in entities if e.text == "Gaza")
    assert loc.confidence == 0.90


def test_country_confidence() -> None:
    entities = extract_entities("Elections held in Germany")
    loc = next(e for e in entities if e.text == "Germany")
    assert loc.confidence == 0.85


def test_coordinate_extraction() -> None:
    entities = extract_entities("Explosion at 34.0522, -118.2437 confirmed by police")
    locations = [e for e in entities if e.entity_type == "location"]
    assert any("34.0522" in e.text and "-118.2437" in e.text for e in locations)


# ── Weapon extraction ──────────────────────────────────────────────────


def test_extract_weapon_term() -> None:
    entities = extract_entities("HIMARS systems were deployed to the front line")
    weapons = [e for e in entities if e.entity_type == "weapon"]
    assert any(e.text == "HIMARS" for e in weapons)


def test_extract_weapon_case_insensitive() -> None:
    entities = extract_entities("A missile struck the depot")
    weapons = [e for e in entities if e.entity_type == "weapon"]
    assert any(e.text.lower() == "missile" for e in weapons)


def test_weapon_confidence() -> None:
    entities = extract_entities("F-16 jets intercepted the drone")
    weapon = next(e for e in entities if e.entity_type == "weapon" and "F-16" in e.text)
    assert weapon.confidence == 0.80


# ── Date extraction ────────────────────────────────────────────────────


def test_extract_date_dmy_format() -> None:
    entities = extract_entities("The ceasefire began on 15 March 2024")
    dates = [e for e in entities if e.entity_type == "date"]
    assert any("15 March 2024" in e.text for e in dates)


def test_extract_date_mdy_format() -> None:
    entities = extract_entities("The summit is scheduled for January 5, 2025")
    dates = [e for e in entities if e.entity_type == "date"]
    assert any("January 5, 2025" in e.text for e in dates)


def test_extract_date_iso_format() -> None:
    entities = extract_entities("Incident logged on 2024-08-15")
    dates = [e for e in entities if e.entity_type == "date"]
    assert any("2024-08-15" in e.text for e in dates)


# ── Quantity extraction ────────────────────────────────────────────────


def test_extract_quantity() -> None:
    entities = extract_entities("At least 150 people displaced after the flood")
    quantities = [e for e in entities if e.entity_type == "quantity"]
    assert any("150 people" in e.text for e in quantities)


def test_extract_casualty_quantity() -> None:
    entities = extract_entities("Officials confirmed 23 killed in the blast")
    quantities = [e for e in entities if e.entity_type == "quantity"]
    assert any("23 killed" in e.text for e in quantities)


# ── Edge cases ─────────────────────────────────────────────────────────


def test_empty_input_returns_empty_list() -> None:
    assert extract_entities("") == []


def test_no_entities_in_plain_text() -> None:
    entities = extract_entities("the quick brown fox jumps over the lazy dog")
    # May return some low-confidence capitalized phrase hits, but no gazetteered ones
    gazetteered = [e for e in entities if e.confidence >= 0.80]
    assert gazetteered == []


def test_all_confidence_scores_valid() -> None:
    text = (
        "Putin and NATO discussed Ukraine crisis. "
        "HIMARS deployed. 100 killed on 2024-01-15."
    )
    entities = extract_entities(text)
    for e in entities:
        assert 0.0 <= e.confidence <= 1.0, f"Invalid confidence {e.confidence} for {e.text}"


def test_entity_positions_populated() -> None:
    text = "NATO summit in Ukraine"
    entities = extract_entities(text)
    for e in entities:
        assert e.start >= 0
        assert e.end >= e.start


def test_deduplicated_entities() -> None:
    text = "Ukraine Ukraine Ukraine"
    entities = extract_entities(text)
    ukraine_locs = [e for e in entities if e.text == "Ukraine" and e.entity_type == "location"]
    assert len(ukraine_locs) == 1


# ── Batch extraction ──────────────────────────────────────────────────


def test_extract_entities_batch() -> None:
    texts = ["NATO summit", "Earthquake in Japan"]
    result = extract_entities_batch(texts)
    assert 0 in result
    assert 1 in result
    assert any(e.text == "NATO" for e in result[0])
    assert any(e.text == "Japan" for e in result[1])
