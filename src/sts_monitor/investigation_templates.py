"""Pre-built investigation templates for common OSINT scenarios.

Each template provides curated connector configs, RSS feeds, subreddits,
search queries, and alert rules to get investigations running immediately.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class InvestigationTemplate:
    """Blueprint for auto-configuring an investigation."""
    name: str
    description: str
    category: str  # geopolitical, local, conspiracy, natural_disaster, conflict, custom
    default_topic: str
    seed_queries: list[str]
    rss_feeds: list[str] = field(default_factory=list)
    subreddits: list[str] = field(default_factory=list)
    connectors: list[str] = field(default_factory=list)  # connector types to enable
    alert_rules: list[dict[str, Any]] = field(default_factory=list)
    priority: int = 50
    tags: list[str] = field(default_factory=list)
    rabbit_trail: bool = False  # enable deep autonomous follow-up
    autopilot_interval_s: int = 300


# ═══════════════════════════════════════════════════════════════════════════
# Geopolitical Templates
# ═══════════════════════════════════════════════════════════════════════════

GEOPOLITICAL_GLOBAL = InvestigationTemplate(
    name="Global Geopolitical Monitor",
    description="Track major geopolitical events, conflicts, sanctions, diplomatic shifts worldwide",
    category="geopolitical",
    default_topic="Global geopolitical events and conflicts",
    seed_queries=[
        "geopolitical conflict 2024 2025",
        "NATO military deployment",
        "sanctions embargo international",
        "diplomatic crisis summit",
        "territorial dispute sovereignty",
        "proxy war insurgency",
    ],
    rss_feeds=[
        "https://feeds.bbci.co.uk/news/world/rss.xml",
        "https://rss.nytimes.com/services/xml/rss/nyt/World.xml",
        "https://feeds.reuters.com/Reuters/worldNews",
        "https://www.aljazeera.com/xml/rss/all.xml",
        "https://www.theguardian.com/world/rss",
        "https://rss.app/feeds/v1.1/tD8YQHVxMqjGZwkE.xml",  # OSINT aggregator
    ],
    subreddits=[
        "worldnews", "geopolitics", "IntelligenceNews",
        "CredibleDefense", "UkrainianConflict",
        "IsraelPalestine", "foreignpolicy",
    ],
    connectors=["gdelt", "acled", "reliefweb", "rss", "reddit"],
    alert_rules=[
        {"name": "rapid_escalation", "min_observations": 10, "min_disputed_claims": 2, "cooldown_seconds": 600},
        {"name": "new_conflict_zone", "min_observations": 5, "min_disputed_claims": 1, "cooldown_seconds": 1800},
    ],
    priority=80,
    tags=["geopolitical", "conflict", "global"],
    rabbit_trail=True,
    autopilot_interval_s=180,
)

UKRAINE_RUSSIA = InvestigationTemplate(
    name="Ukraine-Russia Conflict Monitor",
    description="Deep monitoring of Ukraine-Russia conflict, military movements, negotiations",
    category="geopolitical",
    default_topic="Ukraine Russia conflict military activity",
    seed_queries=[
        "Ukraine Russia frontline update",
        "Crimea Donbas military",
        "NATO Ukraine weapons",
        "Russia sanctions evasion",
        "Ukraine counteroffensive",
        "Zaporizhzhia nuclear",
    ],
    rss_feeds=[
        "https://feeds.bbci.co.uk/news/world/europe/rss.xml",
        "https://www.understandingwar.org/feed",
        "https://liveuamap.com/rss",
    ],
    subreddits=["UkrainianConflict", "ukraine", "CredibleDefense", "CombatFootage"],
    connectors=["gdelt", "acled", "rss", "reddit"],
    priority=90,
    tags=["ukraine", "russia", "conflict", "europe"],
    rabbit_trail=True,
    autopilot_interval_s=120,
)

MIDDLE_EAST = InvestigationTemplate(
    name="Middle East Crisis Monitor",
    description="Track conflicts, tensions, and humanitarian crises across the Middle East",
    category="geopolitical",
    default_topic="Middle East conflict humanitarian crisis",
    seed_queries=[
        "Israel Palestine Gaza conflict",
        "Iran nuclear sanctions",
        "Syria civil war",
        "Yemen Houthi Red Sea",
        "Lebanon Hezbollah",
        "Iraq militia",
    ],
    rss_feeds=[
        "https://www.aljazeera.com/xml/rss/all.xml",
        "https://www.middleeasteye.net/rss",
        "https://feeds.bbci.co.uk/news/world/middle_east/rss.xml",
    ],
    subreddits=["IsraelPalestine", "syriancivilwar", "YemeniCrisis", "iran", "MiddleEastNews"],
    connectors=["gdelt", "acled", "reliefweb", "rss", "reddit"],
    priority=85,
    tags=["middle_east", "conflict", "humanitarian"],
    rabbit_trail=True,
)

CHINA_PACIFIC = InvestigationTemplate(
    name="China & Indo-Pacific Monitor",
    description="Track China military buildup, Taiwan tensions, South China Sea, Belt and Road",
    category="geopolitical",
    default_topic="China military Taiwan South China Sea Indo-Pacific",
    seed_queries=[
        "Taiwan strait military",
        "South China Sea dispute",
        "China military buildup",
        "AUKUS submarine",
        "Belt and Road debt trap",
        "Xinjiang Uyghur",
    ],
    rss_feeds=[
        "https://www.scmp.com/rss/91/feed",
        "https://thediplomat.com/feed/",
        "https://feeds.bbci.co.uk/news/world/asia/rss.xml",
    ],
    subreddits=["China", "taiwan", "geopolitics", "IndoPacificWatch"],
    connectors=["gdelt", "rss", "reddit", "opensky"],
    priority=80,
    tags=["china", "taiwan", "pacific", "military"],
    rabbit_trail=True,
)

# ═══════════════════════════════════════════════════════════════════════════
# Local / Boston / Massachusetts Templates
# ═══════════════════════════════════════════════════════════════════════════

BOSTON_LOCAL = InvestigationTemplate(
    name="Boston & Massachusetts Monitor",
    description="Local news, events, safety, politics, and development in Boston/MA area",
    category="local",
    default_topic="Boston Massachusetts local news events safety",
    seed_queries=[
        "Boston news today",
        "Massachusetts politics legislation",
        "MBTA transit issues Boston",
        "Boston real estate development",
        "Cambridge Somerville news",
        "Massachusetts public safety crime",
    ],
    rss_feeds=[
        "https://www.bostonglobe.com/rss/metro",
        "https://www.boston.com/tag/local-news/feed/",
        "https://www.wgbh.org/news/feed",
        "https://www.universalhub.com/rss.xml",
        "https://patch.com/massachusetts/boston/rss",
        "https://www.masslive.com/arc/outboundfeeds/rss/?outputType=xml",
    ],
    subreddits=["boston", "massachusetts", "BostonSocialClub", "cambridge_ma"],
    connectors=["nws", "rss", "reddit", "fema"],
    alert_rules=[
        {"name": "local_emergency", "min_observations": 3, "min_disputed_claims": 0, "cooldown_seconds": 300},
    ],
    priority=70,
    tags=["boston", "massachusetts", "local"],
    autopilot_interval_s=300,
)

NEW_ENGLAND_WEATHER = InvestigationTemplate(
    name="New England Severe Weather",
    description="Monitor severe weather, nor'easters, hurricanes, flooding in New England",
    category="local",
    default_topic="New England Massachusetts severe weather storm",
    seed_queries=[
        "New England severe weather warning",
        "Boston nor'easter blizzard",
        "Massachusetts flooding",
        "Cape Cod hurricane",
        "New England tornado warning",
    ],
    rss_feeds=[
        "https://alerts.weather.gov/cap/ma.php?x=0",
        "https://forecast.weather.gov/MapClick.php?lat=42.3601&lon=-71.0589&FcstType=digitalDWML",
    ],
    subreddits=["boston", "newengland", "weather"],
    connectors=["nws", "usgs", "fema", "rss"],
    priority=75,
    tags=["weather", "new_england", "boston", "emergency"],
)

# ═══════════════════════════════════════════════════════════════════════════
# Conspiracy / Deep Investigation / Rabbit Trail Templates
# ═══════════════════════════════════════════════════════════════════════════

MEDIA_NARRATIVE_TRACKER = InvestigationTemplate(
    name="Media Narrative & Contradiction Tracker",
    description="Detect conflicting narratives across mainstream and independent media. "
                "Identify stories being suppressed, contradicted, or memory-holed. "
                "Follow the rabbit trail wherever it leads.",
    category="conspiracy",
    default_topic="Media contradictions narrative suppression cover-up",
    seed_queries=[
        "retracted story mainstream media",
        "fact check disputed narrative",
        "whistleblower leak cover up",
        "declassified documents reveal",
        "media blackout censorship",
        "conflicting reports official narrative",
    ],
    rss_feeds=[
        "https://feeds.reuters.com/Reuters/worldNews",
        "https://feeds.bbci.co.uk/news/rss.xml",
        "https://rss.nytimes.com/services/xml/rss/nyt/HomePage.xml",
        "https://www.theguardian.com/world/rss",
        "https://theintercept.com/feed/?rss",
        "https://www.propublica.org/feeds/propublica/main",
    ],
    subreddits=[
        "conspiracy", "HighStrangeness", "actualconspiracies",
        "UnresolvedMysteries", "skeptic", "media_criticism",
    ],
    connectors=["gdelt", "rss", "reddit"],
    alert_rules=[
        {"name": "narrative_shift", "min_observations": 5, "min_disputed_claims": 3, "cooldown_seconds": 600},
        {"name": "high_contradiction", "min_observations": 8, "min_disputed_claims": 5, "cooldown_seconds": 300},
    ],
    priority=85,
    tags=["conspiracy", "media", "narrative", "contradiction"],
    rabbit_trail=True,
    autopilot_interval_s=120,
)

DEEP_STATE_ACCOUNTABILITY = InvestigationTemplate(
    name="Government Accountability & Transparency",
    description="Track government actions, spending, classified programs, FOIA releases, "
                "legislative riders, and potential corruption. Follow financial trails.",
    category="conspiracy",
    default_topic="Government accountability transparency corruption FOIA",
    seed_queries=[
        "FOIA release classified",
        "government spending waste fraud",
        "military industrial complex contract",
        "intelligence agency oversight",
        "surveillance program domestic",
        "lobbying influence legislation",
        "dark money PAC spending",
    ],
    rss_feeds=[
        "https://www.propublica.org/feeds/propublica/main",
        "https://theintercept.com/feed/?rss",
        "https://www.opensecrets.org/feeds/latestnews.xml",
        "https://www.documentcloud.org/feed",
    ],
    subreddits=[
        "actualconspiracies", "WikiLeaks", "privacy",
        "restorethefourth", "politics", "Libertarian",
    ],
    connectors=["gdelt", "rss", "reddit"],
    priority=75,
    tags=["government", "transparency", "FOIA", "corruption"],
    rabbit_trail=True,
    autopilot_interval_s=300,
)

UFO_UAP_TRACKER = InvestigationTemplate(
    name="UAP/UFO Disclosure Tracker",
    description="Track UAP/UFO sightings, government disclosures, congressional hearings, "
                "whistleblower testimony, and military encounters.",
    category="conspiracy",
    default_topic="UAP UFO disclosure sighting military encounter",
    seed_queries=[
        "UAP UFO congressional hearing",
        "Pentagon UFO disclosure",
        "military UAP encounter",
        "UFO whistleblower testimony",
        "AARO report findings",
        "unexplained aerial phenomena",
    ],
    rss_feeds=[
        "https://thedebrief.org/feed/",
        "https://www.liberationtimes.com/feed",
    ],
    subreddits=["UFOs", "UAP", "HighStrangeness", "aliens"],
    connectors=["gdelt", "rss", "reddit", "opensky"],
    priority=60,
    tags=["uap", "ufo", "disclosure", "military"],
    rabbit_trail=True,
)

# ═══════════════════════════════════════════════════════════════════════════
# Natural Disaster Templates
# ═══════════════════════════════════════════════════════════════════════════

EARTHQUAKE_MONITOR = InvestigationTemplate(
    name="Global Earthquake Monitor",
    description="Track significant earthquakes, aftershocks, tsunami warnings worldwide",
    category="natural_disaster",
    default_topic="Earthquake seismic activity tsunami warning",
    seed_queries=["earthquake magnitude damage", "tsunami warning", "seismic activity"],
    subreddits=["Earthquakes", "geology", "TropicalWeather"],
    connectors=["usgs", "gdelt", "rss", "reliefweb"],
    priority=80,
    tags=["earthquake", "tsunami", "natural_disaster"],
    autopilot_interval_s=120,
)

WILDFIRE_MONITOR = InvestigationTemplate(
    name="Wildfire & Fire Monitor",
    description="Track active wildfires, evacuation orders, air quality using NASA FIRMS",
    category="natural_disaster",
    default_topic="Wildfire fire evacuation air quality",
    seed_queries=["wildfire active fire", "evacuation order fire", "air quality index fire"],
    subreddits=["wildfire", "CAwildfires"],
    connectors=["nasa_firms", "nws", "gdelt", "rss"],
    priority=80,
    tags=["wildfire", "fire", "evacuation", "natural_disaster"],
    autopilot_interval_s=180,
)

# ═══════════════════════════════════════════════════════════════════════════
# Template Registry
# ═══════════════════════════════════════════════════════════════════════════

ALL_TEMPLATES: dict[str, InvestigationTemplate] = {
    "geopolitical_global": GEOPOLITICAL_GLOBAL,
    "ukraine_russia": UKRAINE_RUSSIA,
    "middle_east": MIDDLE_EAST,
    "china_pacific": CHINA_PACIFIC,
    "boston_local": BOSTON_LOCAL,
    "new_england_weather": NEW_ENGLAND_WEATHER,
    "media_narrative": MEDIA_NARRATIVE_TRACKER,
    "government_accountability": DEEP_STATE_ACCOUNTABILITY,
    "uap_ufo": UFO_UAP_TRACKER,
    "earthquake": EARTHQUAKE_MONITOR,
    "wildfire": WILDFIRE_MONITOR,
}


def list_templates(category: str | None = None) -> list[dict[str, Any]]:
    """List available templates, optionally filtered by category."""
    results = []
    for key, tmpl in ALL_TEMPLATES.items():
        if category and tmpl.category != category:
            continue
        results.append({
            "key": key,
            "name": tmpl.name,
            "description": tmpl.description,
            "category": tmpl.category,
            "tags": tmpl.tags,
            "rabbit_trail": tmpl.rabbit_trail,
            "priority": tmpl.priority,
        })
    return results


def get_template(key: str) -> InvestigationTemplate | None:
    """Get a template by key."""
    return ALL_TEMPLATES.get(key)


def apply_template(key: str, custom_topic: str | None = None) -> dict[str, Any]:
    """Generate investigation configuration from a template."""
    tmpl = ALL_TEMPLATES.get(key)
    if not tmpl:
        return {"error": f"Unknown template: {key}"}
    return {
        "topic": custom_topic or tmpl.default_topic,
        "seed_query": tmpl.seed_queries[0] if tmpl.seed_queries else None,
        "priority": tmpl.priority,
        "status": "active" if tmpl.rabbit_trail else "open",
        "template_key": key,
        "template_name": tmpl.name,
        "config": {
            "rss_feeds": tmpl.rss_feeds,
            "subreddits": tmpl.subreddits,
            "connectors": tmpl.connectors,
            "alert_rules": tmpl.alert_rules,
            "seed_queries": tmpl.seed_queries,
            "rabbit_trail": tmpl.rabbit_trail,
            "autopilot_interval_s": tmpl.autopilot_interval_s,
        },
    }
