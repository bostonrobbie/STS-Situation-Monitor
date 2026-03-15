"""Collection plan management.

Defines what intelligence to collect, from which sources, on what schedule.
Maps investigation requirements to connector configurations, enabling
automated multi-source data gathering.

Inspired by intelligence community collection management practices:
"Define requirements → map to sources → schedule collection → evaluate results"
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


@dataclass(slots=True)
class CollectionRequirement:
    """A specific intelligence requirement tied to an investigation."""
    name: str
    description: str
    investigation_id: str
    connectors: list[str]  # Which connectors to use
    query: str  # Search query or seed term
    priority: int = 50  # 1-100
    interval_seconds: int = 3600  # How often to collect
    active: bool = True
    filters: dict[str, Any] = field(default_factory=dict)  # Connector-specific filters
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


# ── Default connector configs ───────────────────────────────────────────

DEFAULT_CONNECTOR_CONFIGS: dict[str, dict[str, Any]] = {
    "gdelt": {
        "timespan": "3h",
        "max_records": 75,
        "mode": "ArtList",
    },
    "usgs": {
        "min_magnitude": 4.0,
        "lookback_hours": 24,
        "max_events": 100,
    },
    "nasa_firms": {
        "days": 1,
        "min_confidence": "nominal",
    },
    "acled": {
        "lookback_days": 7,
        "limit": 100,
    },
    "nws": {
        "severity_filter": "Extreme,Severe",
        "status": "actual",
    },
    "fema": {
        "lookback_days": 30,
        "limit": 100,
    },
    "rss": {
        "per_feed_limit": 10,
    },
    "reddit": {
        "per_subreddit_limit": 25,
        "sort": "new",
    },
    "nitter": {
        "per_account_limit": 20,
    },
    "web_scraper": {
        "max_depth": 2,
        "max_pages": 50,
        "delay_between_requests_s": 1.0,
    },
    "search": {
        "max_results": 20,
    },
}


# ── Curated RSS feed collections ────────────────────────────────────────

CURATED_FEEDS: dict[str, list[dict[str, str]]] = {
    "world_news": [
        {"name": "Reuters World", "url": "https://feeds.reuters.com/Reuters/worldNews"},
        {"name": "BBC World", "url": "https://feeds.bbci.co.uk/news/world/rss.xml"},
        {"name": "Al Jazeera", "url": "https://www.aljazeera.com/xml/rss/all.xml"},
        {"name": "AP News", "url": "https://rsshub.app/apnews/topics/apf-topnews"},
        {"name": "France24", "url": "https://www.france24.com/en/rss"},
        {"name": "DW News", "url": "https://rss.dw.com/rdf/rss-en-all"},
    ],
    "conflict_security": [
        {"name": "The War Zone", "url": "https://www.thedrive.com/the-war-zone/feed"},
        {"name": "Defense One", "url": "https://www.defenseone.com/rss/"},
        {"name": "Jane's Defence", "url": "https://www.janes.com/feeds/news"},
        {"name": "IISS", "url": "https://www.iiss.org/rss"},
        {"name": "Crisis Group", "url": "https://www.crisisgroup.org/rss.xml"},
        {"name": "SIPRI", "url": "https://www.sipri.org/rss.xml"},
    ],
    "humanitarian": [
        {"name": "ReliefWeb Updates", "url": "https://reliefweb.int/updates/rss.xml"},
        {"name": "UNHCR News", "url": "https://www.unhcr.org/rss/news.xml"},
        {"name": "WHO Disease Outbreaks", "url": "https://www.who.int/feeds/entity/don/en/rss.xml"},
        {"name": "ICRC News", "url": "https://www.icrc.org/en/rss"},
        {"name": "MSF Press", "url": "https://www.msf.org/rss/all"},
    ],
    "natural_disasters": [
        {"name": "USGS Earthquakes", "url": "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/significant_month.atom"},
        {"name": "NOAA Hazards", "url": "https://www.weather.gov/rss_page.php?site_name=nws"},
        {"name": "GDACS Alerts", "url": "https://www.gdacs.org/xml/rss.xml"},
        {"name": "Volcano Discovery", "url": "https://www.volcanodiscovery.com/rss/news.xml"},
    ],
    "cyber_threat": [
        {"name": "Krebs on Security", "url": "https://krebsonsecurity.com/feed/"},
        {"name": "The Hacker News", "url": "https://feeds.feedburner.com/TheHackersNews"},
        {"name": "BleepingComputer", "url": "https://www.bleepingcomputer.com/feed/"},
        {"name": "Dark Reading", "url": "https://www.darkreading.com/rss.xml"},
        {"name": "CISA Alerts", "url": "https://www.cisa.gov/uscert/ncas/alerts.xml"},
    ],
    "osint_analysis": [
        {"name": "Bellingcat", "url": "https://www.bellingcat.com/feed/"},
        {"name": "The Intercept", "url": "https://theintercept.com/feed/?rss"},
        {"name": "Lawfare", "url": "https://www.lawfaremedia.org/rss.xml"},
        {"name": "Foreign Policy", "url": "https://foreignpolicy.com/feed/"},
        {"name": "War on the Rocks", "url": "https://warontherocks.com/feed/"},
    ],
    "government_press": [
        {"name": "White House Briefings", "url": "https://www.whitehouse.gov/feed/"},
        {"name": "US State Dept", "url": "https://www.state.gov/rss-feed/press-releases/feed/"},
        {"name": "UK FCDO", "url": "https://www.gov.uk/government/organisations/foreign-commonwealth-development-office.atom"},
        {"name": "EU External Action", "url": "https://www.eeas.europa.eu/eeas/press-material_en?f%5B0%5D=press_material_type%3Apress_release&_format=rss"},
    ],
}


def get_curated_feeds(categories: list[str] | None = None) -> list[dict[str, str]]:
    """Get curated RSS feeds, optionally filtered by category."""
    if categories:
        feeds = []
        for cat in categories:
            feeds.extend(CURATED_FEEDS.get(cat, []))
        return feeds
    # Return all feeds
    all_feeds = []
    for feeds in CURATED_FEEDS.values():
        all_feeds.extend(feeds)
    return all_feeds


def list_feed_categories() -> list[dict[str, Any]]:
    """List available feed categories with counts."""
    return [
        {"category": cat, "feed_count": len(feeds), "feeds": feeds}
        for cat, feeds in CURATED_FEEDS.items()
    ]


# ── Collection plan builder ─────────────────────────────────────────────


def build_collection_plan(
    investigation_topic: str,
    seed_query: str | None = None,
    priority: int = 50,
) -> list[CollectionRequirement]:
    """Auto-generate a collection plan for an investigation topic.

    Analyzes the topic text to determine which connectors and RSS feeds
    are most relevant, then creates a set of collection requirements.
    """
    topic_lower = investigation_topic.lower()
    query = seed_query or investigation_topic

    requirements: list[CollectionRequirement] = []

    # Always include GDELT (broad news coverage)
    requirements.append(CollectionRequirement(
        name=f"GDELT: {query[:60]}",
        description=f"Monitor global news for: {investigation_topic}",
        investigation_id="",  # Set by caller
        connectors=["gdelt"],
        query=query,
        priority=priority,
        interval_seconds=3600,  # Hourly
    ))

    # Earthquake/seismic topics
    earthquake_kw = {"earthquake", "seismic", "quake", "tsunami", "tremor", "tectonic", "richter"}
    if any(kw in topic_lower for kw in earthquake_kw):
        requirements.append(CollectionRequirement(
            name=f"USGS Earthquakes: {query[:40]}",
            description="Monitor earthquake activity",
            investigation_id="",
            connectors=["usgs"],
            query=query,
            priority=priority + 10,
            interval_seconds=900,  # Every 15 minutes
            filters={"min_magnitude": 3.0},
        ))

    # Fire/wildfire topics
    fire_kw = {"fire", "wildfire", "blaze", "burn", "arson", "forest fire", "bushfire"}
    if any(kw in topic_lower for kw in fire_kw):
        requirements.append(CollectionRequirement(
            name=f"NASA FIRMS: {query[:40]}",
            description="Monitor fire hotspots via satellite",
            investigation_id="",
            connectors=["nasa_firms"],
            query=query,
            priority=priority + 10,
            interval_seconds=1800,  # Every 30 minutes
        ))

    # Conflict/security topics
    conflict_kw = {"war", "conflict", "military", "attack", "troops", "bomb", "strike",
                   "missile", "protest", "riot", "insurgent", "militia", "terror", "coup"}
    if any(kw in topic_lower for kw in conflict_kw):
        requirements.append(CollectionRequirement(
            name=f"ACLED Conflict: {query[:40]}",
            description="Monitor conflict events",
            investigation_id="",
            connectors=["acled"],
            query=query,
            priority=priority + 10,
            interval_seconds=7200,  # Every 2 hours
        ))

    # Weather/disaster topics
    weather_kw = {"storm", "hurricane", "tornado", "flood", "cyclone", "typhoon",
                  "drought", "blizzard", "weather", "severe"}
    if any(kw in topic_lower for kw in weather_kw):
        requirements.append(CollectionRequirement(
            name=f"NWS Alerts: {query[:40]}",
            description="Monitor severe weather alerts",
            investigation_id="",
            connectors=["nws"],
            query=query,
            priority=priority + 10,
            interval_seconds=900,  # Every 15 minutes
        ))

    # Disaster/FEMA topics
    disaster_kw = {"disaster", "fema", "emergency", "declaration", "evacuation", "relief"}
    if any(kw in topic_lower for kw in disaster_kw):
        requirements.append(CollectionRequirement(
            name=f"FEMA: {query[:40]}",
            description="Monitor disaster declarations",
            investigation_id="",
            connectors=["fema"],
            query=query,
            priority=priority,
            interval_seconds=3600,
        ))

    # Determine relevant RSS feed categories
    rss_categories: list[str] = ["world_news"]  # Always include
    if any(kw in topic_lower for kw in conflict_kw):
        rss_categories.append("conflict_security")
    if any(kw in topic_lower for kw in {"humanitarian", "refugee", "aid", "relief", "crisis", "displaced"}):
        rss_categories.append("humanitarian")
    if any(kw in topic_lower for kw in earthquake_kw | fire_kw | weather_kw | disaster_kw):
        rss_categories.append("natural_disasters")
    if any(kw in topic_lower for kw in {"cyber", "hack", "breach", "malware", "ransomware", "vulnerability"}):
        rss_categories.append("cyber_threat")
    if any(kw in topic_lower for kw in {"osint", "investigation", "intelligence", "disinformation", "propaganda"}):
        rss_categories.append("osint_analysis")

    feeds = get_curated_feeds(list(set(rss_categories)))
    if feeds:
        requirements.append(CollectionRequirement(
            name=f"RSS Feeds: {', '.join(set(rss_categories))}",
            description=f"Monitor {len(feeds)} curated RSS feeds",
            investigation_id="",
            connectors=["rss"],
            query=query,
            priority=priority - 5,
            interval_seconds=1800,  # Every 30 minutes
            filters={"feed_urls": [f["url"] for f in feeds]},
        ))

    # Always include Twitter/Nitter monitoring
    nitter_cats: list[str] = ["geopolitics"]
    if any(kw in topic_lower for kw in conflict_kw):
        nitter_cats.append("conflict")
    if any(kw in topic_lower for kw in {"osint", "investigation", "intelligence"}):
        nitter_cats.append("osint")
    if any(kw in topic_lower for kw in earthquake_kw | fire_kw | weather_kw | disaster_kw):
        nitter_cats.append("natural_disaster")
    requirements.append(CollectionRequirement(
        name=f"Twitter/Nitter: {query[:40]}",
        description=f"Monitor Twitter via Nitter for: {investigation_topic}",
        investigation_id="",
        connectors=["nitter"],
        query=query,
        priority=priority,
        interval_seconds=1800,
        filters={"categories": list(set(nitter_cats))},
    ))

    # Always include web search
    requirements.append(CollectionRequirement(
        name=f"Web Search: {query[:40]}",
        description=f"Search the web for: {investigation_topic}",
        investigation_id="",
        connectors=["search"],
        query=query,
        priority=priority - 5,
        interval_seconds=3600,
    ))

    return requirements


# ── LLM-assisted discovery prompt ───────────────────────────────────────

LLM_DISCOVERY_PROMPT = """You are an OSINT analyst. Given these recent observations,
identify the top emerging stories that deserve investigation.

For each story, provide:
1. A concise headline (under 100 chars)
2. Why it matters (1-2 sentences)
3. Suggested search terms
4. Which data sources to monitor (from: gdelt, usgs, nasa_firms, acled, nws, fema, rss, reddit, nitter, search, web_scraper)
5. Priority (1-100, where 100 is most urgent)

Recent observations:
{observations}

Return a JSON array of objects with keys: headline, why_it_matters, search_terms, data_sources, priority
Return ONLY the JSON array."""


def build_llm_discovery_prompt(observations_text: list[str]) -> str:
    """Build the LLM prompt for story discovery."""
    # Limit to most recent 20 observations to fit context
    sample = observations_text[:20]
    obs_text = "\n".join(f"- {o[:200]}" for o in sample)
    return LLM_DISCOVERY_PROMPT.format(observations=obs_text)
