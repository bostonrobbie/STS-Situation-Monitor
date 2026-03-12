# STS Situation Monitor — Comprehensive Enhancement Plan

## Vision

Transform STS from an API-only intelligence backend into a **unified, real-time OSINT situation monitoring platform** with a map-centric visual dashboard, 15+ live data source connectors, real-time streaming, AI-powered analysis, and the governance/lineage rigor that separates STS from viral competitors like ShadowBroker, WorldMonitor, and GlobalPulse.

The key insight: STS already has the **strongest backend** in the open-source OSINT space (RBAC, audit trails, claim lineage, evidence chains, confidence scoring, job queues). The viral projects have flashy frontends but no governance. We add the visual layer and data breadth on top of the existing foundation.

---

## Phase 1: Data Source Expansion (New Connectors)

### Goal
Expand from 2 connectors (RSS, Reddit) to 12+ covering news, geophysical, transportation, social, and threat intelligence feeds.

### 1.1 GDELT Connector (Priority: Critical)
**File:** `src/sts_monitor/connectors/gdelt.py`

The single most important data source for situation monitoring. Free, no auth, updates every 15 minutes, covers 100+ languages.

- Use the GDELT DOC 2.0 API: `https://api.gdeltproject.org/api/v2/doc/doc`
- Parameters: `query`, `mode=ArtList`, `format=json`, `maxrecords=75`, `timespan` (e.g., `60min`, `24hours`)
- Parse response JSON → extract articles with `url`, `title`, `seendate`, `domain`, `language`, `sourcecountry`
- Map `sourcecountry` to ISO codes for geolocation
- Set `reliability_hint` based on domain trust (known outlets = 0.7, unknown = 0.5)
- Add optional `geo` parameter for GDELT GEO 2.0 API to get geotagged events
- **Config keys:** `STS_GDELT_TIMEOUT_S` (default 15), `STS_GDELT_DEFAULT_TIMESPAN` (default `3h`)

```python
class GDELTConnector:
    name = "gdelt"
    def collect(self, query: str | None = None) -> ConnectorResult: ...
    def collect_geo(self, query: str) -> list[GeoEvent]: ...  # returns lat/lon tagged events
```

### 1.2 USGS Earthquake Connector (Priority: High)
**File:** `src/sts_monitor/connectors/usgs.py`

Free, no auth, real-time, GeoJSON output with lat/lon built in.

- Endpoint: `https://earthquake.usgs.gov/fdsnws/event/1/query`
- Parameters: `format=geojson`, `starttime`, `endtime`, `minmagnitude` (default 4.0)
- Also poll the summary feeds: `https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/significant_hour.geojson`
- Each feature has `geometry.coordinates` [lon, lat, depth] and `properties` (mag, place, time, url, tsunami flag)
- Map to Observation with `source="usgs:earthquake"`, claim = "M{mag} earthquake near {place}", url = USGS event page
- Set `reliability_hint = 0.95` (authoritative government source)
- **Config keys:** `STS_USGS_MIN_MAGNITUDE` (default 4.0), `STS_USGS_TIMEOUT_S` (default 10)

### 1.3 NASA FIRMS Fire Connector (Priority: High)
**File:** `src/sts_monitor/connectors/nasa_firms.py`

Near real-time global wildfire/hotspot detection. Free with MAP_KEY.

- Endpoint: `https://firms.modaps.eosdis.nasa.gov/api/area/csv/{MAP_KEY}/VIIRS_NOAA20_NRT/{area}/{days}`
- Or country endpoint: `https://firms.modaps.eosdis.nasa.gov/api/country/csv/{MAP_KEY}/VIIRS_NOAA20_NRT/{country_code}/{days}`
- Parse CSV response → extract `latitude`, `longitude`, `brightness`, `confidence`, `acq_date`, `acq_time`
- Filter by confidence level (nominal/high only)
- Map to Observation with geolocation metadata
- **Config keys:** `STS_NASA_FIRMS_MAP_KEY`, `STS_NASA_FIRMS_TIMEOUT_S` (default 15)

### 1.4 ACLED Conflict Connector (Priority: High)
**File:** `src/sts_monitor/connectors/acled.py`

Structured conflict and protest event data with geocoding.

- Endpoint: `https://api.acleddata.com/acled/read`
- Parameters: `key`, `email`, `event_date`, `event_date_where=BETWEEN`, `limit=500`
- Returns JSON with `event_type`, `sub_event_type`, `actor1`, `actor2`, `country`, `latitude`, `longitude`, `fatalities`, `notes`, `source`
- Map to Observation with `source="acled:{event_type}"`, claim = notes/description, full geolocation
- Set `reliability_hint = 0.85` (academic research source)
- **Config keys:** `STS_ACLED_API_KEY`, `STS_ACLED_EMAIL`, `STS_ACLED_TIMEOUT_S`

### 1.5 NWS Weather Alerts Connector (Priority: Medium)
**File:** `src/sts_monitor/connectors/nws.py`

US weather warnings and alerts. Free, no auth.

- Endpoint: `https://api.weather.gov/alerts/active`
- Parameters: `status=actual`, `severity=Extreme,Severe` (filter noise)
- Returns GeoJSON FeatureCollection with alert geometry, headline, description, severity, urgency, certainty
- Map to Observation with `source="nws:alert"`, claim = headline + event type
- **Config keys:** `STS_NWS_SEVERITY_FILTER` (default `Extreme,Severe`), `STS_NWS_TIMEOUT_S`

### 1.6 FEMA Disaster Connector (Priority: Medium)
**File:** `src/sts_monitor/connectors/fema.py`

US disaster declarations and emergency management. Free, no auth.

- Endpoint: `https://www.fema.gov/api/open/v2/DisasterDeclarationsSummaries`
- Filter by `$filter=declarationDate gt '{date}'`
- Map to Observation with `source="fema:disaster"`, claim = disaster title + type
- **Config keys:** `STS_FEMA_TIMEOUT_S`

### 1.7 Bluesky/AT Protocol Connector (Priority: Medium)
**File:** `src/sts_monitor/connectors/bluesky.py`

Decentralized social media firehose. Free, no auth for Jetstream.

- Connect to Jetstream WebSocket: `wss://jetstream1.us-east.bsky.network/subscribe`
- Filter by `wantedCollections=app.bsky.feed.post`
- Parse each event for post text, author DID, timestamp, reply context
- Apply keyword filtering against investigation seed_query
- Map to Observation with `source="bluesky:{author}"`, claim = post text
- Set `reliability_hint = 0.4` (social media, unverified)
- **Config keys:** `STS_BLUESKY_JETSTREAM_URL`, `STS_BLUESKY_TIMEOUT_S`
- **Note:** This is a streaming connector — different pattern from polling. Integrate via job queue or dedicated background task.

### 1.8 ADS-B Aircraft Connector (Priority: Medium)
**File:** `src/sts_monitor/connectors/adsb.py`

Aircraft tracking via OpenSky Network. Free, 4000 credits/day.

- Endpoint: `https://opensky-network.org/api/states/all`
- Optional bounding box: `lamin`, `lamax`, `lomin`, `lomax`
- Returns state vectors: icao24, callsign, origin_country, longitude, latitude, altitude, velocity, on_ground
- Filter for military/government aircraft by callsign patterns or ICAO hex ranges
- Map to geolocation layer (not traditional Observation — see Phase 3 geo model)
- **Config keys:** `STS_OPENSKY_USERNAME`, `STS_OPENSKY_PASSWORD` (optional, increases quota)

### 1.9 AIS Maritime Connector (Priority: Medium)
**File:** `src/sts_monitor/connectors/ais.py`

Ship tracking via AISStream.io. Free WebSocket API.

- Connect to `wss://stream.aisstream.io/v0/stream`
- Send subscription message with API key and bounding boxes
- Receive position reports with MMSI, ship name, lat/lon, speed, course, ship type
- Filter for vessels of interest (tankers, military, cargo in key waterways)
- **Config keys:** `STS_AISSTREAM_API_KEY`, `STS_AISSTREAM_BOUNDING_BOXES`

### 1.10 Threat Intelligence Connector (Priority: Low)
**File:** `src/sts_monitor/connectors/threat_intel.py`

Aggregate from free threat intel feeds.

- abuse.ch URLhaus: `https://urlhaus-api.abuse.ch/v1/urls/recent/`
- abuse.ch ThreatFox: `https://threatfox-api.abuse.ch/api/v1/` (POST `{"query": "get_iocs", "days": 1}`)
- AlienVault OTX: `https://otx.alienvault.com/api/v1/pulses/subscribed` (requires free API key)
- Map each IOC to Observation with `source="threatintel:{feed}"`, claim = IOC description
- **Config keys:** `STS_OTX_API_KEY`

### 1.11 Google Trends Connector (Priority: Low)
**File:** `src/sts_monitor/connectors/google_trends.py`

Enhance existing `research.py` trending scanner.

- Parse Google Trends RSS: `https://trends.google.com/trending/rss?geo={country}`
- Extract trending queries with approximate traffic numbers
- Use as investigation seed suggestions and burst-mode triggers

### Implementation Pattern for All Connectors

Every connector follows the existing `Connector` protocol:

```python
class Connector(Protocol):
    name: str
    def collect(self, query: str | None = None) -> ConnectorResult: ...
```

For geo-enabled connectors, extend the Observation dataclass or add metadata:

```python
@dataclass(slots=True)
class GeoObservation(Observation):
    latitude: float | None = None
    longitude: float | None = None
    geo_source: str = ""  # "native" | "geocoded" | "inferred"
```

### Registration in main.py

Add each connector to the ingestion endpoints following the RSS/Reddit pattern:
- New Pydantic request model per connector
- POST `/investigations/{id}/ingest/{connector_name}` endpoint
- Persist observations + ingestion run audit trail
- Add corresponding job type for scheduled ingestion

---

## Phase 2: Database Schema Evolution

### Goal
Add geolocation support, geo-event layers, enhanced entity extraction fields, and the convergence detection table.

### 2.1 Migration 0007: Geolocation Fields
**File:** `alembic/versions/0007_geolocation.py`

Add nullable geo columns to observations:

```sql
ALTER TABLE observations ADD COLUMN latitude FLOAT;
ALTER TABLE observations ADD COLUMN longitude FLOAT;
ALTER TABLE observations ADD COLUMN geo_source VARCHAR(30) DEFAULT '';
ALTER TABLE observations ADD COLUMN connector_type VARCHAR(50) DEFAULT 'rss';
CREATE INDEX ix_observations_geo ON observations (latitude, longitude) WHERE latitude IS NOT NULL;
CREATE INDEX ix_observations_connector_type ON observations (connector_type);
```

Update `ObservationORM` in models.py:

```python
latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
geo_source: Mapped[str] = mapped_column(String(30), default="")
connector_type: Mapped[str] = mapped_column(String(50), default="rss")
```

### 2.2 Migration 0008: Geo Event Layers
**File:** `alembic/versions/0008_geo_layers.py`

New table for transient geo-layer data (aircraft, ships, fires) that isn't traditional observations:

```sql
CREATE TABLE geo_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    layer VARCHAR(40) NOT NULL,          -- 'earthquake', 'fire', 'aircraft', 'vessel', 'conflict', 'weather_alert'
    source_id VARCHAR(120),              -- external ID (USGS event ID, MMSI, ICAO24, etc.)
    title VARCHAR(500) NOT NULL,
    latitude FLOAT NOT NULL,
    longitude FLOAT NOT NULL,
    altitude FLOAT,
    magnitude FLOAT,                     -- earthquake mag, fire brightness, alert severity score
    properties_json TEXT DEFAULT '{}',   -- flexible metadata per layer type
    event_time DATETIME NOT NULL,
    fetched_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    expires_at DATETIME,                 -- TTL for transient data (aircraft positions expire quickly)
    investigation_id VARCHAR(36) REFERENCES investigations(id) ON DELETE SET NULL
);
CREATE INDEX ix_geo_events_layer ON geo_events (layer);
CREATE INDEX ix_geo_events_time ON geo_events (event_time);
CREATE INDEX ix_geo_events_coords ON geo_events (latitude, longitude);
CREATE INDEX ix_geo_events_expires ON geo_events (expires_at);
```

### 2.3 Migration 0009: Convergence Detection
**File:** `alembic/versions/0009_convergence.py`

Track when multiple signal types converge in the same geography:

```sql
CREATE TABLE convergence_zones (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    center_lat FLOAT NOT NULL,
    center_lon FLOAT NOT NULL,
    radius_km FLOAT NOT NULL DEFAULT 50.0,
    signal_count INTEGER NOT NULL,
    signal_types_json TEXT NOT NULL,      -- ["earthquake", "conflict", "fire"]
    severity VARCHAR(20) DEFAULT 'low',  -- low/medium/high/critical
    first_detected_at DATETIME NOT NULL,
    last_updated_at DATETIME NOT NULL,
    resolved_at DATETIME,
    investigation_id VARCHAR(36) REFERENCES investigations(id) ON DELETE SET NULL,
    detail_json TEXT DEFAULT '{}'
);
CREATE INDEX ix_convergence_zones_severity ON convergence_zones (severity);
CREATE INDEX ix_convergence_zones_active ON convergence_zones (resolved_at) WHERE resolved_at IS NULL;
```

### 2.4 Migration 0010: Dashboard Widget Config
**File:** `alembic/versions/0010_dashboard_config.py`

User-configurable dashboard layouts:

```sql
CREATE TABLE dashboard_configs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name VARCHAR(120) NOT NULL,
    owner VARCHAR(120),
    layout_json TEXT NOT NULL DEFAULT '{}',
    active_layers_json TEXT NOT NULL DEFAULT '[]',
    map_center_lat FLOAT DEFAULT 20.0,
    map_center_lon FLOAT DEFAULT 0.0,
    map_zoom FLOAT DEFAULT 2.0,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
```

---

## Phase 3: Real-Time Streaming (SSE)

### Goal
Add Server-Sent Events endpoints so the frontend receives live updates without polling.

### 3.1 Event Bus
**File:** `src/sts_monitor/event_bus.py`

Simple in-process pub/sub for broadcasting events to SSE subscribers:

```python
import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

@dataclass(slots=True)
class STSEvent:
    event_type: str           # "observation", "geo_event", "alert", "convergence", "pipeline_run"
    payload: dict[str, Any]
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))

class EventBus:
    def __init__(self) -> None:
        self._subscribers: list[asyncio.Queue[STSEvent]] = []

    def subscribe(self) -> asyncio.Queue[STSEvent]:
        queue: asyncio.Queue[STSEvent] = asyncio.Queue(maxsize=256)
        self._subscribers.append(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[STSEvent]) -> None:
        self._subscribers = [q for q in self._subscribers if q is not queue]

    async def publish(self, event: STSEvent) -> None:
        dead: list[asyncio.Queue[STSEvent]] = []
        for queue in self._subscribers:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                dead.append(queue)
        for q in dead:
            self._subscribers.remove(q)

event_bus = EventBus()
```

### 3.2 SSE Endpoint
**Add to:** `src/sts_monitor/main.py`

```python
from fastapi.responses import StreamingResponse

@app.get("/events/stream")
async def event_stream(
    layers: str = "",              # comma-separated layer filters
    investigation_id: str = "",    # filter to specific investigation
):
    queue = event_bus.subscribe()
    layer_filter = set(layers.split(",")) if layers else set()

    async def generate():
        try:
            while True:
                event = await queue.get()
                if layer_filter and event.event_type not in layer_filter:
                    continue
                if investigation_id and event.payload.get("investigation_id") != investigation_id:
                    continue
                data = json.dumps({"type": event.event_type, "data": event.payload, "ts": event.timestamp.isoformat()})
                yield f"event: {event.event_type}\ndata: {data}\n\n"
        finally:
            event_bus.unsubscribe(queue)

    return StreamingResponse(generate(), media_type="text/event-stream")
```

### 3.3 Publish Events from Existing Flows

Modify ingestion endpoints and pipeline to publish events:
- After persisting new observations → `event_bus.publish(STSEvent("observation", {...}))`
- After pipeline run completes → `event_bus.publish(STSEvent("pipeline_run", {...}))`
- After alert triggers → `event_bus.publish(STSEvent("alert", {...}))`
- After geo_events inserted → `event_bus.publish(STSEvent("geo_event", {...}))`

---

## Phase 4: Convergence Detection Engine

### Goal
Implement the WorldMonitor-inspired pattern: "One signal is noise. Three or four converging in the same location is the signal worth surfacing."

### 4.1 Convergence Detector
**File:** `src/sts_monitor/convergence.py`

```python
from math import radians, sin, cos, sqrt, atan2

def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distance between two points on Earth in km."""
    ...

def detect_convergence(
    geo_events: list[GeoEvent],
    radius_km: float = 50.0,
    min_signal_types: int = 3,
    time_window_hours: int = 24,
) -> list[ConvergenceZone]:
    """Cluster geo_events by proximity and count distinct signal types."""
    ...
```

### 4.2 Integration
- Run convergence detection after each batch of geo_events is inserted
- Publish convergence events via SSE
- Auto-create investigation suggestions for critical convergence zones
- Display convergence zones as pulsing circles on the map

---

## Phase 5: Enhanced Pipeline & AI

### Goal
Upgrade the signal pipeline with entity extraction, geocoding, and LLM-powered claim analysis.

### 5.1 Entity Extraction
**File:** `src/sts_monitor/entities.py`

Lightweight regex + pattern-based entity extraction (no heavy NLP dependencies):

- Extract country/city names from observation text using a gazetteer lookup
- Extract organization names using known-entity lists
- Extract coordinates from text (e.g., "40.7128° N, 74.0060° W")
- Map extracted locations to lat/lon for auto-geotagging observations

### 5.2 Enhanced Pipeline Stages
**Modify:** `src/sts_monitor/pipeline.py`

Add new pipeline stages after deduplication:
1. **Entity extraction** → tag observations with extracted entities
2. **Geocoding** → attempt to assign lat/lon to observations that lack native geolocation
3. **Cross-source correlation** → find observations from different sources about the same event/entity
4. **Convergence check** → trigger convergence detection when geo-located observations cluster

### 5.3 LLM-Powered Claim Analysis
**Enhance:** `src/sts_monitor/llm.py`

Add new LLM prompt templates:
- **Claim extraction**: "Extract atomic verifiable claims from this text"
- **Entity extraction**: "Extract people, organizations, and locations from this text"
- **Situation brief**: "Given these N observations, write a situation brief covering: likely true, disputed, unknown, next watch"
- **Cross-investigation correlation**: "Given these observations from different investigations, identify connections"

---

## Phase 6: API Enhancements

### Goal
Add new endpoints to support the dashboard frontend, geo-layers, and real-time operations.

### 6.1 Geo Events API
```
GET  /geo/events?layers=earthquake,fire,conflict&bbox=lat1,lon1,lat2,lon2&since=ISO8601
POST /geo/events/refresh/{layer}    -- trigger a refresh of a specific geo layer
GET  /geo/layers                    -- list available layers with status/count/last_updated
```

### 6.2 Convergence API
```
GET  /convergence/zones?active=true&min_severity=medium
GET  /convergence/zones/{id}
POST /convergence/detect            -- trigger manual convergence scan
```

### 6.3 Enhanced Dashboard API
```
GET  /dashboard/live                -- real-time stats (observations/min, active connectors, etc.)
GET  /dashboard/map-data            -- aggregated geo data for map rendering
GET  /dashboard/timeline?since=ISO8601&until=ISO8601  -- time-bucketed event counts
GET  /dashboard/top-entities        -- most-mentioned entities across all investigations
GET  /dashboard/source-health       -- connector status, last successful fetch, error rates
```

### 6.4 Investigation Enhancement
```
GET  /investigations/{id}/geo       -- all geolocated observations for an investigation
GET  /investigations/{id}/entities  -- extracted entities for an investigation
GET  /investigations/{id}/timeline  -- time-series of observation counts
```

---

## Phase 7: Frontend Dashboard

### Goal
Build a React + Vite SPA with MapLibre GL, Recharts, and dark theme that serves as the unified "command center."

### 7.1 Tech Stack
- **Vite** — build tool (fast HMR, small bundles)
- **React 18** — UI framework
- **TypeScript** — type safety
- **MapLibre GL JS** — map rendering (free, no token, WebGL)
- **Recharts** — charts/graphs
- **Tailwind CSS** — utility-first styling (dark theme default)
- **react-use-websocket** or native EventSource — for SSE consumption
- **Cytoscape.js** — claim/evidence graph visualization
- **TanStack Query** — data fetching + caching

### 7.2 Directory Structure
```
frontend/
├── index.html
├── package.json
├── tsconfig.json
├── tailwind.config.ts
├── vite.config.ts
├── public/
│   └── favicon.svg
├── src/
│   ├── main.tsx
│   ├── App.tsx
│   ├── api/
│   │   ├── client.ts            -- API client (fetch wrapper)
│   │   ├── hooks.ts             -- TanStack Query hooks for each endpoint
│   │   └── sse.ts               -- SSE connection manager
│   ├── components/
│   │   ├── layout/
│   │   │   ├── Sidebar.tsx
│   │   │   ├── TopBar.tsx
│   │   │   └── MainLayout.tsx
│   │   ├── map/
│   │   │   ├── MapView.tsx              -- MapLibre GL main component
│   │   │   ├── LayerToggle.tsx          -- toggle geo layers
│   │   │   ├── ClusterMarkers.tsx       -- observation clusters
│   │   │   ├── ConvergenceOverlay.tsx   -- pulsing convergence zones
│   │   │   ├── HeatmapLayer.tsx         -- density heatmap
│   │   │   └── layers/
│   │   │       ├── EarthquakeLayer.tsx
│   │   │       ├── FireLayer.tsx
│   │   │       ├── ConflictLayer.tsx
│   │   │       ├── WeatherAlertLayer.tsx
│   │   │       ├── AircraftLayer.tsx
│   │   │       └── VesselLayer.tsx
│   │   ├── dashboard/
│   │   │   ├── LiveCounters.tsx         -- observations/min, active investigations, alerts
│   │   │   ├── ConfidenceTrend.tsx      -- confidence over time chart
│   │   │   ├── SourceHealth.tsx         -- connector status panel
│   │   │   ├── TopEntities.tsx          -- most-mentioned entities
│   │   │   └── RecentAlerts.tsx         -- latest alert events
│   │   ├── timeline/
│   │   │   ├── EventTimeline.tsx        -- chronological event stream
│   │   │   └── TimeSlider.tsx           -- time range filter
│   │   ├── investigation/
│   │   │   ├── InvestigationList.tsx
│   │   │   ├── InvestigationDetail.tsx
│   │   │   ├── ClaimMatrix.tsx          -- claims x evidence grid
│   │   │   └── EvidenceGraph.tsx        -- Cytoscape.js claim-evidence graph
│   │   ├── search/
│   │   │   ├── SearchBar.tsx
│   │   │   └── SearchResults.tsx
│   │   └── common/
│   │       ├── ConfidenceBadge.tsx
│   │       ├── SourceBadge.tsx
│   │       └── SeverityChip.tsx
│   ├── pages/
│   │   ├── DashboardPage.tsx            -- main landing: map + counters + timeline
│   │   ├── InvestigationsPage.tsx       -- investigation management
│   │   ├── InvestigationDetailPage.tsx  -- single investigation deep-dive
│   │   ├── SearchPage.tsx               -- cross-investigation search
│   │   └── SettingsPage.tsx             -- connector config, API keys
│   ├── stores/
│   │   ├── mapStore.ts                  -- map state (center, zoom, active layers)
│   │   ├── sseStore.ts                  -- real-time event state
│   │   └── filterStore.ts              -- global time/severity/source filters
│   └── styles/
│       └── globals.css                  -- Tailwind base + dark theme tokens
```

### 7.3 Main Dashboard Page Layout

```
┌──────────────────────────────────────────────────────────────┐
│  [STS Logo]  Dashboard  Investigations  Search  Settings     │
├────────┬─────────────────────────────────────────────────────┤
│        │                                                     │
│ LAYERS │              INTERACTIVE MAP                        │
│ □ Conf │         (MapLibre GL - full width)                  │
│ □ Quak │                                                     │
│ □ Fire │    [convergence zone pulsing]  [event clusters]     │
│ □ Weat │                                                     │
│ □ ADS-B│    [fire hotspots]  [earthquake markers]            │
│ □ AIS  │                                                     │
│ □ News │                                                     │
│        ├─────────────────────────────────────────────────────┤
│ STATS  │                                                     │
│ 1.2k/h │    EVENT TIMELINE                    RECENT ALERTS  │
│ 12 inv │    [time-bucketed bar chart]         [alert feed]   │
│ 3 alrt │    [========|====|===========]       ⚠ Alert 1      │
│ 0.73 c │    [time slider control]             ⚠ Alert 2      │
│        │                                                     │
│ HEALTH │    TOP ENTITIES          CONFIDENCE TREND            │
│ ● GDLT │    Person X: 47 refs    [line chart over time]      │
│ ● USGS │    Org Y: 31 refs                                   │
│ ● FIRMS│    Place Z: 28 refs                                 │
│ ○ AIS  │                                                     │
└────────┴─────────────────────────────────────────────────────┘
```

### 7.4 Investigation Detail Page Layout

```
┌──────────────────────────────────────────────────────────────┐
│  ← Back  Investigation: "Topic Name"  [Status: Open] [P: 85]│
├──────────────────────────────────────────────────────────────┤
│                                                              │
│  SITUATION BRIEF (AI-generated)              CONFIDENCE: 0.73│
│  ┌─────────────────────────────────────────────────────────┐ │
│  │ Likely True: ...                                        │ │
│  │ Disputed: ...                                           │ │
│  │ Unknown: ...                                            │ │
│  │ Watch Next: ...                                         │ │
│  └─────────────────────────────────────────────────────────┘ │
│                                                              │
│  ┌─────────────────────┐  ┌────────────────────────────────┐ │
│  │ CLAIM-EVIDENCE GRAPH│  │ OBSERVATION TIMELINE           │ │
│  │ (Cytoscape.js)      │  │ [chronological feed]           │ │
│  │                     │  │                                │ │
│  │   [Claim A]──[Obs1] │  │ 14:23 rss: Reuters reports... │ │
│  │      │       [Obs2] │  │ 14:19 reddit: User claims...  │ │
│  │   [Claim B]──[Obs3] │  │ 14:15 gdelt: Event detected.. │ │
│  │      │              │  │ 14:10 usgs: M5.2 earthquake.. │ │
│  │   [Claim C]  DISPUT │  │                                │ │
│  └─────────────────────┘  └────────────────────────────────┘ │
│                                                              │
│  GEO VIEW (map of this investigation's observations)         │
│  ┌─────────────────────────────────────────────────────────┐ │
│  │         [map with investigation-specific markers]        │ │
│  └─────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────┘
```

### 7.5 Frontend Build Integration

Add to docker-compose.yml:

```yaml
  frontend:
    build:
      context: ./frontend
      dockerfile: Dockerfile
    ports:
      - "3000:3000"
    depends_on:
      - api
```

Add a `frontend/Dockerfile`:

```dockerfile
FROM node:20-alpine AS build
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM nginx:alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 3000
```

FastAPI serves both API and static frontend via a fallback route, or Nginx/Caddy reverse-proxies both.

---

## Phase 8: Scheduled Data Collection

### Goal
Auto-collect from all connectors on configurable intervals using the existing job queue.

### 8.1 New Job Types

Add to `jobs.py` and register in `main.py`:

| Job Type | Default Interval | Description |
|----------|-----------------|-------------|
| `ingest-gdelt` | 15 min | GDELT DOC API poll |
| `ingest-usgs` | 5 min | USGS earthquake feed |
| `ingest-firms` | 30 min | NASA FIRMS fire data |
| `ingest-acled` | 6 hours | ACLED conflict events |
| `ingest-nws` | 10 min | NWS weather alerts |
| `ingest-fema` | 1 hour | FEMA disaster declarations |
| `ingest-bluesky` | continuous | Bluesky Jetstream (streaming) |
| `ingest-adsb` | 2 min | OpenSky aircraft positions |
| `ingest-ais` | continuous | AIS vessel positions (streaming) |
| `refresh-convergence` | 5 min | Run convergence detection |
| `cleanup-expired-geo` | 10 min | Prune expired geo_events |

### 8.2 Auto-Schedule Setup

On app startup, auto-create default schedules if not already present:

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    # ... existing setup ...
    ensure_default_schedules(session)
    yield
```

### 8.3 Enhanced Job Worker

Update `scripts/job_worker.py` to handle the new job types and support streaming connectors as long-lived background tasks.

---

## Phase 9: Export & Interoperability

### Goal
Export data in standard intelligence formats for interop with other tools.

### 9.1 Export Formats
- **GeoJSON**: `/export/geojson/{investigation_id}` — all geo-located observations as GeoJSON FeatureCollection
- **STIX 2.1**: `/export/stix/{investigation_id}` — claims and evidence as STIX bundles for threat intel sharing
- **CSV**: `/export/csv/{investigation_id}` — flat observation export
- **Markdown Report**: `/export/report/{investigation_id}` — formatted situation report
- **RSS/Atom**: Already exists at `/investigations/{id}/feed.rss` — enhance with geo tags (GeoRSS)

### 9.2 Import Formats
- **STIX 2.1 Import**: `/import/stix` — ingest STIX bundles as observations
- **GeoJSON Import**: `/import/geojson` — ingest geotagged event collections
- **MISP Event Import**: `/import/misp` — ingest MISP events (future)

---

## Phase 10: Configuration & Deployment

### 10.1 Updated .env.example

Add all new config keys with sensible defaults:

```env
# === New Connectors ===
STS_GDELT_TIMEOUT_S=15
STS_GDELT_DEFAULT_TIMESPAN=3h
STS_USGS_MIN_MAGNITUDE=4.0
STS_USGS_TIMEOUT_S=10
STS_NASA_FIRMS_MAP_KEY=
STS_NASA_FIRMS_TIMEOUT_S=15
STS_ACLED_API_KEY=
STS_ACLED_EMAIL=
STS_ACLED_TIMEOUT_S=15
STS_NWS_SEVERITY_FILTER=Extreme,Severe
STS_NWS_TIMEOUT_S=10
STS_FEMA_TIMEOUT_S=10
STS_BLUESKY_JETSTREAM_URL=wss://jetstream1.us-east.bsky.network/subscribe
STS_OPENSKY_USERNAME=
STS_OPENSKY_PASSWORD=
STS_AISSTREAM_API_KEY=
STS_OTX_API_KEY=

# === Frontend ===
STS_FRONTEND_DEV_PORT=3000
STS_MAP_STYLE_URL=https://demotiles.maplibre.org/style.json
STS_MAP_DEFAULT_CENTER=20.0,0.0
STS_MAP_DEFAULT_ZOOM=2
```

### 10.2 Updated docker-compose.yml

Add frontend service and optional Ollama:

```yaml
services:
  # ... existing services ...

  frontend:
    build: ./frontend
    ports:
      - "3000:3000"
    depends_on:
      - api

  ollama:
    image: ollama/ollama:latest
    ports:
      - "11434:11434"
    volumes:
      - ollama_data:/root/.ollama
    deploy:
      resources:
        reservations:
          devices:
            - capabilities: [gpu]

volumes:
  # ... existing ...
  ollama_data:
```

### 10.3 Updated pyproject.toml Dependencies

```toml
dependencies = [
  # ... existing ...
  "sse-starlette>=2.0.0",       # SSE support for FastAPI
  "geopy>=2.4.0",               # geocoding utilities
  "websockets>=12.0",           # WebSocket client for AIS/Bluesky
]
```

---

## Implementation Order (Recommended)

### Sprint 1: Foundation (Weeks 1-2)
1. Database migrations 0007-0010 (geo fields, geo_events, convergence, dashboard_config)
2. Update ObservationORM with new fields
3. GDELT connector (highest-value single addition)
4. USGS earthquake connector
5. NASA FIRMS fire connector
6. Event bus + SSE endpoint
7. Publish events from existing ingestion flows

### Sprint 2: More Data + API (Weeks 3-4)
8. ACLED conflict connector
9. NWS weather alerts connector
10. FEMA disaster connector
11. Bluesky Jetstream connector
12. Geo events API endpoints
13. Enhanced dashboard API endpoints
14. Convergence detection engine
15. Convergence API endpoints
16. Entity extraction (lightweight regex/gazetteer)

### Sprint 3: Frontend MVP (Weeks 5-7)
17. Scaffold React + Vite + TypeScript + Tailwind project
18. API client + TanStack Query hooks
19. SSE connection manager
20. MapLibre GL map component with layer toggles
21. Earthquake, fire, conflict layers
22. Dashboard page: map + live counters + source health
23. Event timeline component
24. Investigation list + detail pages
25. Basic search page

### Sprint 4: Advanced Frontend (Weeks 8-9)
26. Convergence zone overlay (pulsing circles on map)
27. Claim-evidence graph (Cytoscape.js)
28. Confidence trend chart
29. Investigation geo view
30. Time slider filter
31. Dark theme polish
32. Responsive layout

### Sprint 5: Transportation + Streaming (Weeks 10-11)
33. ADS-B aircraft connector + layer
34. AIS maritime connector + layer
35. Streaming connector patterns (WebSocket consumers as background tasks)
36. Threat intelligence connector
37. Google Trends connector enhancement
38. Auto-scheduling on startup

### Sprint 6: Export, Polish, Tests (Weeks 12-13)
39. GeoJSON export
40. STIX 2.1 export
41. CSV export
42. Enhanced markdown report with citations
43. GeoRSS enhancement for existing feed
44. Tests for all new connectors (unit + integration)
45. Tests for convergence detection
46. Tests for SSE streaming
47. Tests for frontend (Vitest + React Testing Library)
48. Performance testing with large geo datasets
49. Documentation updates

---

## Key Design Decisions

### 1. SSE over WebSockets
Dashboards are server-to-client push. SSE is simpler, works over standard HTTP, auto-reconnects, and FastAPI supports it natively via `StreamingResponse`. No need for WebSocket complexity.

### 2. MapLibre GL over Leaflet
MapLibre is WebGL-accelerated, handles 100k+ markers smoothly with clustering, is fully open source (no token), and is what ShadowBroker and WorldMonitor both use. Leaflet is simpler but can't handle the data density we're targeting.

### 3. Separate geo_events table
Transient data (aircraft positions, ship tracks) doesn't belong in the observations table. geo_events has TTL (`expires_at`) for auto-cleanup and is optimized for spatial queries.

### 4. Convergence detection runs server-side
Not in the frontend. The backend has the full picture across all sources and can trigger alerts + auto-investigations. The frontend just renders the results.

### 5. All connectors follow the existing Protocol
No framework changes. New connectors implement `Connector.collect()` → `ConnectorResult`. Geo-enabled connectors additionally populate the `geo_events` table directly.

### 6. Zero required API keys
Like the viral projects: every connector with a free/no-auth data source works out of the box. Optional API keys unlock additional sources (NASA FIRMS, ACLED, AIS). Dashboard panels for unconfigured sources show a "Configure in Settings" placeholder.

### 7. Frontend served alongside API
Single deployment: FastAPI serves the Vite build as static files, with API routes taking priority. One container, one port. Caddy/Nginx optional for production.
