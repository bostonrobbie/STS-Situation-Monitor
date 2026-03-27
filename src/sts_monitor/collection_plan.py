"""Collection plan management.

Defines what intelligence to collect, from which sources, on what schedule.
Maps investigation requirements to connector configurations, enabling
automated multi-source data gathering.

Inspired by intelligence community collection management practices:
"Define requirements → map to sources → schedule collection → evaluate results"
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
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
}


# ── Curated RSS feed collections ────────────────────────────────────────

CURATED_FEEDS: dict[str, list[dict[str, str]]] = {
    # ── Greater Boston & Eastern MA ──────────────────────────────────────
    "boston_metro": [
        {"name": "Boston Globe", "url": "https://www.bostonglobe.com/rss/current/bigpicture"},
        {"name": "Boston Herald", "url": "https://www.bostonherald.com/feed/"},
        {"name": "WBUR News", "url": "https://www.wbur.org/feed/rss/news"},
        {"name": "WCVB Boston", "url": "https://www.wcvb.com/topstories-rss"},
        {"name": "GBH News", "url": "https://www.wgbh.org/news/feed"},
        {"name": "Universal Hub", "url": "https://www.universalhub.com/rss.xml"},
        {"name": "Patch Boston", "url": "https://patch.com/massachusetts/boston/rss"},
        {"name": "Patch Cambridge", "url": "https://patch.com/massachusetts/cambridge/rss"},
        {"name": "Patch Somerville", "url": "https://patch.com/massachusetts/somerville/rss"},
        {"name": "Patch Brookline", "url": "https://patch.com/massachusetts/brookline/rss"},
        {"name": "Patch Newton", "url": "https://patch.com/massachusetts/newton/rss"},
        {"name": "Patch Quincy", "url": "https://patch.com/massachusetts/quincy/rss"},
        {"name": "Google News Boston", "url": "https://news.google.com/rss/search?q=Boston+Massachusetts+news&hl=en-US&gl=US&ceid=US:en"},
    ],
    # ── Central Massachusetts (Worcester & surrounding) ──────────────────
    "central_ma": [
        {"name": "Worcester Telegram", "url": "https://www.telegram.com/rss"},
        {"name": "MassLive", "url": "https://www.masslive.com/arc/outboundfeeds/rss/?outputType=xml"},
        {"name": "Patch Worcester", "url": "https://patch.com/massachusetts/worcester/rss"},
        {"name": "Patch Framingham", "url": "https://patch.com/massachusetts/framingham/rss"},
        {"name": "Patch Marlborough", "url": "https://patch.com/massachusetts/marlborough/rss"},
        {"name": "Patch Shrewsbury", "url": "https://patch.com/massachusetts/shrewsbury/rss"},
        {"name": "Patch Grafton-Millbury", "url": "https://patch.com/massachusetts/grafton-millbury/rss"},
        {"name": "Patch Holden-Rutland", "url": "https://patch.com/massachusetts/holden-rutland/rss"},
        {"name": "Patch Leominster", "url": "https://patch.com/massachusetts/leominster/rss"},
        {"name": "Patch Fitchburg", "url": "https://patch.com/massachusetts/fitchburg/rss"},
        {"name": "Google News Worcester", "url": "https://news.google.com/rss/search?q=Worcester+Massachusetts+news&hl=en-US&gl=US&ceid=US:en"},
        {"name": "Google News Central MA", "url": "https://news.google.com/rss/search?q=%22Central+Massachusetts%22+news&hl=en-US&gl=US&ceid=US:en"},
        {"name": "Google News MetroWest", "url": "https://news.google.com/rss/search?q=MetroWest+Massachusetts+news&hl=en-US&gl=US&ceid=US:en"},
    ],
    # ── Western Massachusetts (Springfield, Pioneer Valley, Berkshires) ──
    "western_ma": [
        {"name": "Berkshire Eagle", "url": "https://www.berkshireeagle.com/search/?f=rss&t=article&l=50"},
        {"name": "Patch Springfield", "url": "https://patch.com/massachusetts/springfield/rss"},
        {"name": "Patch Northampton", "url": "https://patch.com/massachusetts/northampton/rss"},
        {"name": "Patch Amherst", "url": "https://patch.com/massachusetts/amherst/rss"},
        {"name": "Patch Westfield", "url": "https://patch.com/massachusetts/westfield/rss"},
        {"name": "Patch Agawam", "url": "https://patch.com/massachusetts/agawam/rss"},
        {"name": "Google News Springfield MA", "url": "https://news.google.com/rss/search?q=Springfield+Massachusetts+news&hl=en-US&gl=US&ceid=US:en"},
        {"name": "Google News Pioneer Valley", "url": "https://news.google.com/rss/search?q=%22Pioneer+Valley%22+Massachusetts+news&hl=en-US&gl=US&ceid=US:en"},
        {"name": "Google News Berkshires", "url": "https://news.google.com/rss/search?q=Berkshires+Massachusetts+news&hl=en-US&gl=US&ceid=US:en"},
        {"name": "Google News Pittsfield", "url": "https://news.google.com/rss/search?q=Pittsfield+Massachusetts+news&hl=en-US&gl=US&ceid=US:en"},
        {"name": "Google News Western MA", "url": "https://news.google.com/rss/search?q=%22Western+Massachusetts%22+news&hl=en-US&gl=US&ceid=US:en"},
    ],
    # ── North Shore, Merrimack Valley, Lowell ────────────────────────────
    "north_shore_merrimack": [
        {"name": "Patch Salem", "url": "https://patch.com/massachusetts/salem/rss"},
        {"name": "Patch Beverly", "url": "https://patch.com/massachusetts/beverly/rss"},
        {"name": "Patch Gloucester", "url": "https://patch.com/massachusetts/gloucester/rss"},
        {"name": "Patch Peabody", "url": "https://patch.com/massachusetts/peabody/rss"},
        {"name": "Patch Lowell", "url": "https://patch.com/massachusetts/lowell/rss"},
        {"name": "Patch Lawrence", "url": "https://patch.com/massachusetts/lawrence/rss"},
        {"name": "Patch Haverhill", "url": "https://patch.com/massachusetts/haverhill/rss"},
        {"name": "Patch Andover", "url": "https://patch.com/massachusetts/andover/rss"},
        {"name": "Google News North Shore MA", "url": "https://news.google.com/rss/search?q=%22North+Shore%22+Massachusetts+news&hl=en-US&gl=US&ceid=US:en"},
        {"name": "Google News Merrimack Valley", "url": "https://news.google.com/rss/search?q=%22Merrimack+Valley%22+Massachusetts+news&hl=en-US&gl=US&ceid=US:en"},
        {"name": "Google News Lowell MA", "url": "https://news.google.com/rss/search?q=Lowell+Massachusetts+news&hl=en-US&gl=US&ceid=US:en"},
    ],
    # ── South Shore, South Coast, Fall River, New Bedford ────────────────
    "south_shore_coast": [
        {"name": "Patch Plymouth", "url": "https://patch.com/massachusetts/plymouth/rss"},
        {"name": "Patch Brockton", "url": "https://patch.com/massachusetts/brockton/rss"},
        {"name": "Patch Taunton", "url": "https://patch.com/massachusetts/taunton/rss"},
        {"name": "Patch Marshfield", "url": "https://patch.com/massachusetts/marshfield/rss"},
        {"name": "Patch Hingham", "url": "https://patch.com/massachusetts/hingham/rss"},
        {"name": "Google News South Shore MA", "url": "https://news.google.com/rss/search?q=%22South+Shore%22+Massachusetts+news&hl=en-US&gl=US&ceid=US:en"},
        {"name": "Google News New Bedford MA", "url": "https://news.google.com/rss/search?q=%22New+Bedford%22+Massachusetts+news&hl=en-US&gl=US&ceid=US:en"},
        {"name": "Google News Fall River MA", "url": "https://news.google.com/rss/search?q=%22Fall+River%22+Massachusetts+news&hl=en-US&gl=US&ceid=US:en"},
        {"name": "Google News South Coast MA", "url": "https://news.google.com/rss/search?q=%22South+Coast%22+Massachusetts+news&hl=en-US&gl=US&ceid=US:en"},
    ],
    # ── Cape Cod & Islands ───────────────────────────────────────────────
    "cape_islands": [
        {"name": "Cape Cod Times", "url": "https://www.capecodtimes.com/rss"},
        {"name": "Patch Barnstable-Hyannis", "url": "https://patch.com/massachusetts/barnstable-hyannis/rss"},
        {"name": "Patch Falmouth", "url": "https://patch.com/massachusetts/falmouth/rss"},
        {"name": "Google News Cape Cod", "url": "https://news.google.com/rss/search?q=%22Cape+Cod%22+news&hl=en-US&gl=US&ceid=US:en"},
        {"name": "Google News Martha's Vineyard", "url": "https://news.google.com/rss/search?q=%22Martha%27s+Vineyard%22+news&hl=en-US&gl=US&ceid=US:en"},
        {"name": "Google News Nantucket", "url": "https://news.google.com/rss/search?q=Nantucket+news&hl=en-US&gl=US&ceid=US:en"},
    ],
    # ── Statewide Government & Public Safety ─────────────────────────────
    "massachusetts_government": [
        {"name": "Mass.gov News", "url": "https://www.mass.gov/news/feed"},
        {"name": "Boston.gov News", "url": "https://www.boston.gov/news/feed"},
        {"name": "MA State Police", "url": "https://www.mass.gov/orgs/massachusetts-state-police/news/feed"},
        {"name": "MEMA Alerts", "url": "https://www.mass.gov/orgs/massachusetts-emergency-management-agency/news/feed"},
        {"name": "MassDOT", "url": "https://www.mass.gov/orgs/massachusetts-department-of-transportation/news/feed"},
        {"name": "MA DPH", "url": "https://www.mass.gov/orgs/department-of-public-health/news/feed"},
        {"name": "MA AG Office", "url": "https://www.mass.gov/orgs/attorney-generals-office/news/feed"},
        {"name": "MA DEP", "url": "https://www.mass.gov/orgs/massachusetts-department-of-environmental-protection/news/feed"},
        {"name": "MA Courts", "url": "https://www.mass.gov/orgs/trial-court/news/feed"},
    ],
    "massachusetts_public_safety": [
        {"name": "Boston Fire Dept", "url": "https://www.boston.gov/departments/fire-operations/news/feed"},
        {"name": "Boston Police News", "url": "https://bpdnews.com/feed"},
        {"name": "MBTA Alerts", "url": "https://www.mbta.com/news/feed"},
        {"name": "Google News MA Crime", "url": "https://news.google.com/rss/search?q=Massachusetts+crime+police+arrest&hl=en-US&gl=US&ceid=US:en"},
        {"name": "Google News MA Fire", "url": "https://news.google.com/rss/search?q=Massachusetts+fire+emergency&hl=en-US&gl=US&ceid=US:en"},
        {"name": "Google News MA Traffic", "url": "https://news.google.com/rss/search?q=Massachusetts+traffic+accident+crash&hl=en-US&gl=US&ceid=US:en"},
        {"name": "Google News MBTA", "url": "https://news.google.com/rss/search?q=MBTA+delays+service&hl=en-US&gl=US&ceid=US:en"},
    ],
    # ── Weather & Environment ────────────────────────────────────────────
    "weather_environment": [
        {"name": "NWS Boston", "url": "https://alerts.weather.gov/cap/ma.php?x=1"},
        {"name": "USGS NE Earthquakes", "url": "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/2.5_day.atom"},
        {"name": "Google News MA Weather", "url": "https://news.google.com/rss/search?q=Massachusetts+weather+storm+forecast&hl=en-US&gl=US&ceid=US:en"},
        {"name": "Google News MA Flooding", "url": "https://news.google.com/rss/search?q=Massachusetts+flood+warning&hl=en-US&gl=US&ceid=US:en"},
    ],
    # ── Statewide Google News (catches everything else) ──────────────────
    "massachusetts_general": [
        {"name": "Google News MA Breaking", "url": "https://news.google.com/rss/search?q=Massachusetts+breaking+news&hl=en-US&gl=US&ceid=US:en"},
        {"name": "Google News MA Today", "url": "https://news.google.com/rss/search?q=Massachusetts+news+today&hl=en-US&gl=US&ceid=US:en"},
        {"name": "Google News MA Schools", "url": "https://news.google.com/rss/search?q=Massachusetts+schools+education&hl=en-US&gl=US&ceid=US:en"},
        {"name": "Google News MA Health", "url": "https://news.google.com/rss/search?q=Massachusetts+health+hospital&hl=en-US&gl=US&ceid=US:en"},
        {"name": "Google News MA Business", "url": "https://news.google.com/rss/search?q=Massachusetts+business+economy+jobs&hl=en-US&gl=US&ceid=US:en"},
        {"name": "Google News MA Politics", "url": "https://news.google.com/rss/search?q=Massachusetts+governor+legislature+politics&hl=en-US&gl=US&ceid=US:en"},
        {"name": "Google News MA Housing", "url": "https://news.google.com/rss/search?q=Massachusetts+housing+real+estate+rent&hl=en-US&gl=US&ceid=US:en"},
    ],
    # ── New England Regional ─────────────────────────────────────────────
    "new_england_regional": [
        {"name": "NECN", "url": "https://www.necn.com/feed/"},
        {"name": "AP New England", "url": "https://rsshub.app/apnews/topics/apf-topnews"},
        {"name": "Google News New England", "url": "https://news.google.com/rss/search?q=%22New+England%22+news&hl=en-US&gl=US&ceid=US:en"},
    ],
    # ── Cyber Threat (not geo-specific) ──────────────────────────────────
    "cyber_threat": [
        {"name": "Krebs on Security", "url": "https://krebsonsecurity.com/feed/"},
        {"name": "The Hacker News", "url": "https://feeds.feedburner.com/TheHackersNews"},
        {"name": "BleepingComputer", "url": "https://www.bleepingcomputer.com/feed/"},
        {"name": "CISA Alerts", "url": "https://www.cisa.gov/uscert/ncas/alerts.xml"},
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

    # Always include NWS for local weather alerts
    requirements.append(CollectionRequirement(
        name=f"NWS MA Alerts: {query[:60]}",
        description="Monitor Massachusetts severe weather alerts",
        investigation_id="",
        connectors=["nws"],
        query=query,
        priority=priority + 10,
        interval_seconds=900,  # Every 15 minutes
    ))

    # Earthquake/seismic topics
    earthquake_kw = {"earthquake", "seismic", "quake", "tsunami", "tremor", "tectonic", "richter"}
    if any(kw in topic_lower for kw in earthquake_kw):
        requirements.append(CollectionRequirement(
            name=f"USGS Earthquakes: {query[:40]}",
            description="Monitor New England earthquake activity",
            investigation_id="",
            connectors=["usgs"],
            query=query,
            priority=priority + 10,
            interval_seconds=900,
            filters={"min_magnitude": 2.0},
        ))

    # Fire/wildfire topics
    fire_kw = {"fire", "wildfire", "blaze", "burn", "arson", "forest fire"}
    if any(kw in topic_lower for kw in fire_kw):
        requirements.append(CollectionRequirement(
            name=f"NASA FIRMS MA: {query[:40]}",
            description="Monitor fire hotspots in Massachusetts via satellite",
            investigation_id="",
            connectors=["nasa_firms"],
            query=query,
            priority=priority + 10,
            interval_seconds=1800,
        ))

    # Weather/disaster topics
    weather_kw = {"storm", "hurricane", "tornado", "flood", "nor'easter", "noreaster",
                  "drought", "blizzard", "weather", "severe", "snow", "ice", "heat"}
    if any(kw in topic_lower for kw in weather_kw):
        requirements.append(CollectionRequirement(
            name=f"FEMA MA: {query[:40]}",
            description="Monitor Massachusetts disaster declarations",
            investigation_id="",
            connectors=["fema"],
            query=query,
            priority=priority,
            interval_seconds=3600,
        ))

    # Determine relevant RSS feed categories — always include MA core
    rss_categories: list[str] = ["massachusetts_news", "massachusetts_government"]
    if any(kw in topic_lower for kw in {"police", "crime", "fire", "safety", "accident",
                                         "shooting", "arrest", "traffic", "mbta", "transit"}):
        rss_categories.append("massachusetts_public_safety")
    if any(kw in topic_lower for kw in earthquake_kw | fire_kw | weather_kw |
           {"disaster", "fema", "emergency", "flood", "storm"}):
        rss_categories.append("weather_environment")
    if any(kw in topic_lower for kw in {"cyber", "hack", "breach", "malware", "ransomware"}):
        rss_categories.append("cyber_threat")
    rss_categories.append("new_england_regional")

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

    return requirements


# ── LLM-assisted discovery prompt ───────────────────────────────────────

LLM_DISCOVERY_PROMPT = """You are a Massachusetts local intelligence analyst. Given these recent observations from the greater Massachusetts area, identify the top emerging local stories that deserve investigation.

Focus on: local public safety, weather impacts, infrastructure (MBTA, roads, utilities), municipal government actions, environmental concerns, public health, and community events that affect daily life in Massachusetts.

For each story, provide:
1. A concise headline (under 100 chars)
2. Why it matters to Massachusetts residents (1-2 sentences)
3. Suggested search terms
4. Which data sources to monitor (from: nws, fema, usgs, nasa_firms, rss, reddit, webcams)
5. Priority (1-100, where 100 is most urgent)
6. Affected areas within Massachusetts (cities/towns/regions)

Recent observations:
{observations}

Return a JSON array of objects with keys: headline, why_it_matters, search_terms, data_sources, priority, affected_areas
Return ONLY the JSON array."""


def build_llm_discovery_prompt(observations_text: list[str]) -> str:
    """Build the LLM prompt for story discovery."""
    # Limit to most recent 20 observations to fit context
    sample = observations_text[:20]
    obs_text = "\n".join(f"- {o[:200]}" for o in sample)
    return LLM_DISCOVERY_PROMPT.format(observations=obs_text)
