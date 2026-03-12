# OSINT Data Sources & APIs - Technical Reference

Comprehensive catalogue of free/freemium APIs and data sources for situation monitoring.
Last researched: 2026-03-12.

---

## 1. NEWS AGGREGATION APIs

### 1.1 NewsAPI.org
- **URL**: https://newsapi.org
- **Auth**: API key (free registration)
- **Free tier**: 100 requests/day, articles delayed 24 hours, 1-month archive
- **Data format**: JSON
- **Coverage**: Headlines and articles from 80,000+ sources globally
- **Limitations**: Free tier is dev/test only, no production use. No full-text on free tier.
- **Paid**: From $40/month
- **Integration**: Simple REST GET with `?apiKey=` parameter

### 1.2 GNews API
- **URL**: https://gnews.io
- **Auth**: API key
- **Free tier**: 100 requests/day, max 1 request/second
- **Data format**: JSON
- **Coverage**: 80,000+ sources worldwide, up to 6 years historical
- **Limitations**: Free tier is non-commercial only. Basic filters, no enrichment.
- **Integration**: REST API, `GET /api/v4/search?q=keyword&token=KEY`

### 1.3 MediaStack
- **URL**: https://mediastack.com
- **Auth**: API key
- **Free tier**: 500 calls/month (no live news on free plan)
- **Data format**: JSON
- **Coverage**: 7,000+ sources, 50+ countries, 13 languages
- **Paid**: From $24.99/month for 10k calls
- **Limitations**: Free tier does not include live/real-time news data

### 1.4 Currents API
- **URL**: https://currentsapi.services
- **Auth**: API key
- **Free tier**: 600 requests/day
- **Data format**: JSON
- **Coverage**: 14,000+ sources, 78 languages
- **Integration**: REST, `GET /v1/latest-news` or `/v1/search`

### 1.5 TheNewsAPI
- **URL**: https://www.thenewsapi.com
- **Auth**: API key
- **Free tier**: 3 requests/day (very limited free tier)
- **Data format**: JSON
- **Coverage**: 50,000+ sources, 50+ countries, 30+ languages, 1M+ articles/week
- **Features**: Sentiment analysis, entity extraction on paid plans

### 1.6 Newsdata.io
- **URL**: https://newsdata.io
- **Auth**: API key
- **Free tier**: 200 credits/day (~2,000 articles), 12-hour delay, no full content
- **Data format**: JSON, CSV, Excel export
- **Coverage**: 87,000+ sources, 206 countries, 89 languages
- **Limitations**: No historical data on free plan. Search limited to 100 chars.
- **Commercial use**: Allowed on free tier
- **Paid**: From $199.99/month

### 1.7 World News API (API League)
- **URL**: https://worldnewsapi.com
- **Auth**: API key
- **Free tier**: 500 requests/day
- **Data format**: JSON
- **Coverage**: Thousands of sources, 86+ languages, 210+ countries
- **Features**: Sentiment analysis, entity extraction, 30-day historical on free tier
- **Paid**: From $35/month

### 1.8 NewsAPI.ai (Event Registry)
- **URL**: https://newsapi.ai
- **Auth**: API key
- **Free tier**: Full-text access, advanced enrichment, limited to past 30 days
- **Data format**: JSON
- **Features**: Clustering, concept-level filtering, sentiment, enrichment
- **Limitations**: Historical data beyond 30 days requires paid plan

### 1.9 Newscatcher API
- **URL**: https://www.newscatcherapi.com
- **Auth**: API key
- **Free tier**: No permanent free tier. Possible sandbox/trial only.
- **Paid**: From ~$29/month
- **Coverage**: 70,000+ sources, enrichment pipelines

### 1.10 Perigon
- **URL**: https://perigon.io
- **Auth**: API key
- **Free tier**: Unknown/limited trial. Contact for pricing.
- **Features**: AI-powered tagging (topic, event, sentiment, location, org, product)
- **Data format**: JSON

### 1.11 GDELT (Global Database of Events, Language, and Tone)
- **URL**: https://www.gdeltproject.org
- **Auth**: None for raw data; API key for GDELT Cloud
- **Free tier**: 100% free and open. Unlimited.
- **Data format**: CSV (raw), JSON (API), BigQuery SQL
- **Coverage**: Entire world, 100+ languages, updated every 15 minutes since 1979
- **Features**: 300+ event categories (CAMEO coded), actors, locations, Goldstein scale (-10 to +10), tone/sentiment, Global Knowledge Graph
- **Access methods**:
  - Raw CSV downloads (2.5TB+ per year)
  - Google BigQuery (free quota)
  - GDELT Analysis Service (web)
  - Python: `pip install gdelt` (gdeltPyR)
- **Caveats**: Automatic coding means noise, duplicates, and unreliable sources mixed with legitimate ones. Not self-curated.
- **Best for**: High-volume event monitoring, protest/conflict tracking, media analysis

---

## 2. SOCIAL MEDIA MONITORING

### 2.1 Bluesky / AT Protocol (Jetstream)
- **URL**: https://docs.bsky.app/docs/advanced-guides/firehose
- **Auth**: None required (public WebSocket)
- **Free tier**: Completely free, no API keys needed
- **Data format**: JSON over WebSocket (optionally zstd compressed, ~56% smaller)
- **Connection**: `wss://jetstream2.us-east.bsky.network/subscribe?wantedCollections=app.bsky.feed.post`
- **Filtering**: Up to 100 collection prefixes, up to 10,000 DIDs
- **Bandwidth**: Raw firehose = 4-8 GB/hour; Jetstream is lighter
- **Coverage**: All public posts, likes, reposts, follows, blocks, profile updates in real-time
- **4 public instances** operated by Bluesky team
- **Caveats**: Not cryptographically self-authenticating (unlike raw firehose). Not formally part of the AT Protocol spec long-term.
- **Client libraries**: JavaScript, Python, Ruby, Rust

### 2.2 Mastodon / Fediverse
- **URL**: https://docs.joinmastodon.org/methods/streaming/
- **Auth**: OAuth access token required (since v4.2.0, anonymous streaming removed)
- **Free tier**: Free, but requires account on an instance
- **Data format**: Server-Sent Events or WebSocket, JSON payloads
- **Endpoints**:
  - `stream_public` - all public statuses (local or remote)
  - `stream_hashtag` - statuses with specific hashtag
  - `stream_user` - authenticated user's timeline
  - `stream_list` - specific list
- **Instance discovery**: https://fediverse.observer
- **OSINT tools**: Masto (Python), Geogramint (geolocation), Telepathy
- **Caveats**: Decentralized = must connect to many instances for comprehensive coverage

### 2.3 Reddit API
- **URL**: https://www.reddit.com/dev/api/
- **Auth**: OAuth2 required (all unauthenticated traffic blocked)
- **Free tier**: 100 queries/minute (QPM) for non-commercial use (personal projects, academic research, open-source tools)
- **Data format**: JSON
- **Limitations**:
  - 1,000-post ceiling per subreddit listing
  - Failed requests count toward rate limits
  - Sensitive data categories require explicit Reddit permission
  - Pre-approval required for many use cases since 2025
- **Commercial**: $0.24 per 1,000 API calls; enterprise tiers at $12,000+/year
- **Subreddit streaming**: Use `GET /r/{subreddit}/new.json` with polling

### 2.4 Telegram Channel Monitoring
- **URL**: https://core.telegram.org/api (official MTProto API)
- **Auth**: Telegram API credentials (api_id + api_hash from https://my.telegram.org)
- **Free tier**: Free with Telegram account
- **Data format**: JSON (via Telethon/Pyrogram wrappers)
- **Key tools**:
  - **Telethon** (Python): Full Telegram client library, can scrape channels/groups
  - **Telepathy**: OSINT toolkit for investigating Telegram chats
  - **Tosint**: Extracts intelligence from bot tokens and chat IDs
  - **Geogramint**: OSINT geolocation tool for Telegram
- **Approach**: Use Telethon to subscribe to channels and receive new messages in real-time
- **Caveats**: Must join channels to monitor them. Rate limits enforced by Telegram. Some features restricted after Telegram founder's arrest (Aug 2024).

### 2.5 Discord OSINT
- **URL**: https://discord.com/developers/docs
- **Auth**: Bot token or user token
- **Free tier**: Free bot creation, but ToS restricts automated data collection
- **Key tools**:
  - Discord History Tracker (browser script for local chat saves)
  - ReconXplorer (all-in-one OSINT + Discord automation)
  - Discord OSINT Assistant bot
  - Unfurl (timestamp extraction from Discord IDs)
- **Search operators**: `from:`, `mentions:`, `has:link/embed/file`, `before:date`
- **Caveats**: Self-botting violates Discord ToS. Use read-only approaches where possible.

---

## 3. GOVERNMENT / INSTITUTIONAL DATA FEEDS

### 3.1 UN OCHA ReliefWeb API
- **URL**: https://api.reliefweb.int/v2/
- **Docs**: https://apidoc.reliefweb.int/
- **Auth**: `appname` parameter required (pre-approved appname needed from Nov 2025)
- **Free tier**: Completely free
- **Data format**: JSON
- **Endpoints**: `/reports`, `/disasters`, `/countries`, `/jobs`, `/training`, `/sources`
- **Example**: `curl -POST 'https://api.reliefweb.int/v2/reports?appname=YOUR-APP' -d '{"limit": 10}'`
- **Coverage**: Global humanitarian reports, maps, infographics from trusted sources
- **Client libraries**: PHP, Go

### 3.2 Humanitarian Data Exchange (HDX)
- **URL**: https://data.humdata.org
- **Auth**: API key for write; read is open
- **Free tier**: Completely free
- **Data format**: CSV, JSON, XLS (CKAN-based)
- **Coverage**: 20,000+ humanitarian datasets from 250+ organizations
- **API**: CKAN API at `https://data.humdata.org/api/3/action/`

### 3.3 WHO Disease Outbreak News API
- **URL**: `https://www.who.int/api/news/diseaseoutbreaknews`
- **Auth**: None
- **Free tier**: Completely free
- **Data format**: JSON
- **Coverage**: Global disease outbreaks, epidemiology, assessments, advisories
- **Additional**: WHO GHO OData API at `https://www.who.int/data/gho/info/gho-odata-api`

### 3.4 disease.sh (Open Disease Data)
- **URL**: https://disease.sh
- **Auth**: None
- **Free tier**: Completely free, open-source
- **Data format**: JSON
- **Coverage**: COVID-19, influenza, global disease statistics
- **Performance**: 100/100 page insight score

### 3.5 OFAC Sanctions List (U.S. Treasury)
- **URL**: https://sanctionslist.ofac.treas.gov
- **Auth**: None for data downloads; API available
- **Free tier**: Completely free
- **Data format**: XML, CSV, JSON, delimited text
- **Coverage**: SDN List, Consolidated (non-SDN) List, custom datasets
- **Programmatic access**: SLS API for custom dataset retrieval
- **Open-source tool**: Moov OFAC (Go library + HTTP API) - https://github.com/cardonator/ofac

### 3.6 OpenSanctions (Multi-source)
- **URL**: https://www.opensanctions.org
- **API**: https://api.opensanctions.org
- **Auth**: API key for higher volume
- **Free tier**: Free for non-commercial use. 320+ data sources.
- **Data format**: JSON, CSV (tabular), FTMV format
- **Coverage**: UN, EU, OFAC, UK, plus PEPs and watchlists globally
- **Updates**: Daily
- **Paid**: Required for commercial use

### 3.7 EU Sanctions
- **EU Sanctions Map**: https://www.sanctionsmap.eu (browsable, no API)
- **EU Data Portal**: https://data.europa.eu (consolidated list downloadable in XML/CSV)
- **OpenSanctions EU dataset**: https://www.opensanctions.org/datasets/eu_fsf/

### 3.8 SIPRI Databases
- **URL**: https://www.sipri.org/databases
- **Arms Transfers DB**: https://armstransfers.sipri.org (1950-present)
- **Auth**: None
- **Free tier**: Completely free
- **Data format**: RTF, HTML (web); CSV via POST request; Pandas DataFrame via unofficial Python wrapper
- **No official API** - use unofficial wrapper: https://github.com/benryan58/sipri_arms
- **Additional databases**: Arms embargoes, Top 100 arms companies, Military expenditure, Multilateral peace operations

### 3.9 ICC (International Criminal Court)
- **Cases**: https://www.icc-cpi.int/cases
- **Case Law DB**: https://legal-tools.org/cld (6,000+ legal findings since 2004)
- **Auth**: None
- **Free tier**: Completely free
- **No official API** - web search interface only. Consider scraping or manual monitoring.
- **RSS**: Check ICC website for press release feeds

### 3.10 ICJ (International Court of Justice)
- **Cases**: https://www.icj-cij.org/cases (201 cases since 1947)
- **Auth**: None
- **Free tier**: Completely free
- **No official API** - browse/search via website, PDF document search
- **UN Summaries**: https://legal.un.org/icjsummaries/

---

## 4. THREAT INTELLIGENCE FEEDS

### 4.1 AlienVault OTX (Open Threat Exchange)
- **URL**: https://otx.alienvault.com
- **Auth**: Free account required for API key
- **Free tier**: Generous/effectively unlimited for standard use. Commercial use allowed.
- **Data format**: JSON (STIX/TAXII compatible)
- **Coverage**: Community-contributed IOCs (IPs, domains, URLs, file hashes), pulses, threat reports
- **Integration**: REST API, DirectConnect SDK, SIEM integrations
- **Best for**: Most generous free threat intel platform

### 4.2 VirusTotal
- **URL**: https://www.virustotal.com/api/v3/
- **Auth**: API key (free registration)
- **Free tier**: 500 requests/day, 4 requests/minute
- **Data format**: JSON
- **Coverage**: File, URL, domain, IP scanning against 70+ AV engines
- **Limitations**: Free tier is non-commercial only. Commercial license starts ~$75K/year.

### 4.3 AbuseIPDB
- **URL**: https://www.abuseipdb.com/api
- **Auth**: API key (free registration)
- **Free tier**: 1,000 requests/day
- **Data format**: JSON
- **Coverage**: IP reputation, abuse reports, confidence scores
- **Paid**: $5-$150/month for higher limits

### 4.4 Shodan
- **URL**: https://api.shodan.io
- **Auth**: API key
- **Free tier**: ~50 search queries/month, no API credits (IP lookups free, no search credits)
- **Data format**: JSON
- **Coverage**: Internet-wide device/service scanning, banners, vulnerabilities
- **Membership** (one-time $49): 100 query credits/month
- **Paid API**: 10,000+ credits/month
- **Python**: `pip install shodan`

### 4.5 GreyNoise
- **URL**: https://api.greynoise.io
- **Docs**: https://docs.greynoise.io
- **Auth**: Free account API key
- **Free tier (Community API)**: Unlimited IP lookups (with account), 1 endpoint only
- **Without auth**: 10 lookups/day
- **Data format**: JSON
- **Coverage**: Internet background noise, mass scanning, known-benign scanners
- **Limitation**: Community API returns basic classification only (noise/not noise/RIOT)

### 4.6 abuse.ch Feeds
- **URL**: https://abuse.ch
- **Auth**: None for most feeds
- **Free tier**: Completely free
- **Data format**: CSV, JSON
- **Feeds**:
  - **URLhaus**: Malicious URLs - https://urlhaus.abuse.ch/api/
  - **MalBazaar**: Malware samples - https://bazaar.abuse.ch/api/
  - **ThreatFox**: IOCs - https://threatfox.abuse.ch/api/
  - **Feodo Tracker**: Botnet C2 - https://feodotracker.abuse.ch/
  - **SSL Blacklist**: Malicious SSL certs

### 4.7 MISP Feeds (Open Source)
- **URL**: https://www.misp-project.org
- **Free tier**: Completely free (open-source platform)
- **Data format**: MISP JSON, STIX
- **Coverage**: Aggregates community threat intel feeds; sector-specific IOCs
- **Integration**: Deploy your own MISP instance, or consume published feeds

---

## 5. TRANSPORTATION / LOGISTICS

### 5.1 OpenSky Network (Aircraft)
- **URL**: https://opensky-network.org
- **API docs**: https://openskynetwork.github.io/opensky-api/
- **Auth**: OAuth2 (free account)
- **Free tier**: 4,000 API credits/day (8,000 if contributing ADS-B receiver)
- **Data format**: JSON
- **Coverage**: Live + historical aircraft positions globally
- **Features**: State vectors, flight tracks, aircraft metadata DB (CSV download)
- **Limitations**: Non-commercial use only. 1-hour historical lookback, 5-second resolution.
- **Libraries**: Python, R, MATLAB

### 5.2 ADS-B Exchange (Aircraft)
- **URL**: https://www.adsbexchange.com
- **API (RapidAPI)**: Personal/non-commercial use, low-cost
- **Auth**: RapidAPI key
- **Free tier**: Limited free via RapidAPI; enthusiast use permitted
- **Data format**: JSON
- **Endpoints**: Aircraft within X NM of point (max 100NM), by Mode-S hex, by callsign
- **Enterprise API**: Requires commercial license for production use
- **Alternative**: ADSBHub (https://www.adsbhub.org) - free data sharing exchange

### 5.3 FlightRadar24 (Aircraft)
- **URL**: https://fr24api.flightradar24.com
- **Auth**: API key + subscription
- **Free tier**: Sandbox environment for testing (no production credits)
- **Paid**: From $9/month (Explorer, 30k-60k credits/month)
- **Data format**: JSON
- **Coverage**: Real-time positions, historical tracks, airline/airport info
- **SDKs**: Official JavaScript + Python

### 5.4 AISHub (Vessel Tracking - Free)
- **URL**: https://www.aishub.net
- **Auth**: Requires contributing live AIS data from your receiver
- **Free tier**: Free with data sharing reciprocity
- **Data format**: JSON, XML, CSV
- **Rate limit**: Max 1 request/minute
- **Coverage**: Global AIS vessel positions from community network

### 5.5 MarineTraffic / Kpler (Vessel Tracking)
- **URL**: https://www.marinetraffic.com/en/p/api-services
- **Auth**: API key (paid plans)
- **Free tier**: No free API tier. Web interface has limited free access.
- **Data format**: JSON, XML
- **Coverage**: Live AIS, historical tracks, port calls, predictive ETAs, vessel database
- **Paid**: Credit-based system, enterprise pricing

### 5.6 VesselFinder API
- **URL**: https://api.vesselfinder.com
- **Auth**: API key (credit-based)
- **Free tier**: None (credit purchase required)
- **Data format**: JSON, XML

---

## 6. ENVIRONMENTAL / DISASTER

### 6.1 USGS Earthquake Hazards API
- **URL**: https://earthquake.usgs.gov/fdsnws/event/1/
- **Auth**: None
- **Free tier**: Completely free, no limits stated
- **Data format**: GeoJSON, CSV, KML, QuakeML
- **Coverage**: Global earthquakes, real-time + historical (all magnitudes)
- **Features**: ShakeMaps, PAGER alerts, DYFI reports, moment tensors
- **Example**: `GET /query?format=geojson&minmagnitude=5&starttime=2026-03-01`
- **Additional**: Tweet Earthquake Dispatch, GeoServe (places/regions)

### 6.2 NASA EONET (Earth Observatory Natural Event Tracker)
- **URL**: https://eonet.gsfc.nasa.gov/docs/v3
- **Auth**: None
- **Free tier**: Completely free
- **Data format**: JSON (GeoJSON), also available via GraphQL
- **Coverage**: Curated near-real-time natural events: wildfires, dust storms, tropical cyclones, volcanic eruptions, sea/lake ice, severe storms
- **Features**: Links to satellite imagery sources for each event

### 6.3 NASA FIRMS (Fire Information for Resource Management System)
- **URL**: https://firms.modaps.eosdis.nasa.gov/api/
- **Auth**: MAP_KEY from free Earthdata Login
- **Free tier**: Completely free
- **Data format**: JSON, CSV, SHP, KML
- **Endpoints**:
  - Area API: fire data within bounding box
  - Country API: fire data by country code
  - Data Availability API: check dataset status
- **Coverage**: Near-real-time active fire detections (MODIS + VIIRS satellites)
- **Python tutorial**: https://firms.modaps.eosdis.nasa.gov/academy/data_api/

### 6.4 Copernicus Climate Data Store (CDS)
- **URL**: https://cds.climate.copernicus.eu
- **Auth**: Personal Access Token (free registration)
- **Free tier**: Completely free, open, unrestricted
- **Data format**: NetCDF, GRIB, CSV (depending on dataset)
- **Python client**: `pip install cdsapi`
- **Coverage**: Global climate reanalysis, satellite observations, seasonal forecasts, climate projections
- **Config**: Write token to `~/.cdsapirc` file

### 6.5 Copernicus Emergency Management Service (CEMS)
- **Early Warning Data Store**: https://ewds.climate.copernicus.eu
- **Auth**: Same CDS API credentials
- **Free tier**: Completely free
- **Coverage**: Flood forecasting (GloFAS), fire danger forecasting (EFFIS), drought monitoring

### 6.6 Copernicus Data Space Ecosystem (Sentinel Satellite Data)
- **URL**: https://dataspace.copernicus.eu
- **Auth**: Free registration required
- **Free tier**: Completely free (EU open data policy)
- **APIs**: STAC, openEO, Sentinel Hub, OData, OpenSearch
- **Coverage**: Sentinel-1 (SAR), Sentinel-2 (optical), Sentinel-3 (ocean/land), Sentinel-5P (atmospheric)
- **Use cases**: Change detection, flood mapping, vegetation monitoring, air quality
- **Note**: Old Open Access Hub (scihub.copernicus.eu) was shut down Nov 2023

### 6.7 NOAA Climate Data API
- **URL**: https://www.ncei.noaa.gov/access/services/data/v1
- **Auth**: Token (free from https://www.ncdc.noaa.gov/cdo-web/token)
- **Free tier**: Completely free
- **Data format**: JSON, CSV
- **Coverage**: Global historical weather and climate observations
- **Note**: Old endpoint `/cdo-web/api/v2` is deprecated; use new `/access/services/data/v1`

### 6.8 EMSC (European-Mediterranean Seismological Centre)
- **URL**: https://www.seismicportal.eu
- **Auth**: None
- **Free tier**: Completely free
- **Data format**: JSON, QuakeML (FDSN-compliant web services)
- **Coverage**: European and Mediterranean seismic events, real-time + historical

---

## 7. GEOPOLITICAL EVENT DATABASES

### 7.1 GDELT (see Section 1.11 above)
- Updated every 15 minutes, CAMEO-coded, 1979-present, 100% free

### 7.2 ACLED (Armed Conflict Location & Event Data)
- **URL**: https://acleddata.com
- **API docs**: https://acleddata.com/acled-api-documentation
- **Auth**: Free myACLED account required
- **Free tier**: Free for public use (registration required)
- **Data format**: JSON (API), CSV/Excel (download)
- **Coverage**: Political violence and protest events globally. ~205K events/year.
- **Features**: Actor coding, event types (battles, explosions, protests, riots, violence against civilians, strategic developments), fatality estimates, geolocation
- **R package**: `acledR` - https://dtacled.github.io/acledR/
- **Updates**: Weekly
- **Annual products**: Conflict Index, Conflict Watchlist

### 7.3 UCDP (Uppsala Conflict Data Program)
- **URL**: https://ucdp.uu.se
- **Downloads**: https://ucdp.uu.se/downloads/
- **Auth**: None
- **Free tier**: Completely free
- **Data format**: CSV, Excel
- **Coverage**: Organized violence globally since 1946 (state-based, non-state, one-sided)
- **Key datasets**:
  - UCDP GED (Georeferenced Event Dataset): individual events with coordinates
  - UCDP Candidate Events: near-real-time monthly releases (~1 month lag)
  - Battle-Related Deaths Dataset
  - Peace Agreements Dataset
- **Current version**: 25.1 (Oct 2025), 250+ conflict entries
- **R package**: `conflictr`

### 7.4 ICEWS / POLECAT
- **ICEWS**: Global event data 1995-2023 (DARPA-funded, now legacy)
- **POLECAT** (successor): Machine-learning coded events from 1,000+ news sources in 7 languages
  - Uses PLOVER ontology (successor to CAMEO)
  - Millions of time-stamped, geolocated events from 2018+
  - Updated hourly, posted weekly
  - **Access**: Harvard Dataverse / DARPA distribution
- **Data format**: CSV/TSV

### 7.5 Phoenix Event Data
- **URL**: Various academic repositories
- **Coverage**: Machine-coded political events from news sources
- **Coding**: CAMEO taxonomy
- **Status**: Academic project, less actively maintained than GDELT/ACLED

### 7.6 Global Terrorism Database (GTD)
- **URL**: https://www.start.umd.edu/gtd/
- **Auth**: Registration required
- **Free tier**: Free for academic/research use
- **Coverage**: 200,000+ terrorist attacks worldwide, 1970-present
- **Data format**: CSV/Excel
- **Note**: GTD ended data collection in 2021; check for updates

---

## 8. RSS / ATOM FEEDS

### 8.1 Curated OSINT Feed Collections

**FeedSpot Top 35 OSINT RSS Feeds**: https://rss.feedspot.com/osint_rss_feeds/
- OSINT Combine, OSINTME, Knowmad OSINT, and more

**Start.me OSINT RSS Feeds**: https://start.me/p/1kBAvp/osint-rss-feeds

**FeedSpot Top 60 Defense RSS Feeds**: https://rss.feedspot.com/defense_rss_feeds/

**Awesome OSINT (GitHub)**: https://github.com/jivoi/awesome-osint

**T_Baxter80 OPML collection**: 70+ curated feeds in importable OPML format
- Published on Medium / Pastebin (search "Enhancing OSINT Collection using RSS Feeds")

### 8.2 Key Government / Military Feeds

| Source | RSS URL pattern |
|--------|----------------|
| U.S. Department of Defense | https://www.defense.gov/DesktopModules/ArticleCS/RSS.ashx |
| U.S. Department of State | https://www.state.gov/rss-feed/ |
| UK Gov (FCDO) | https://www.gov.uk/government/organisations/foreign-commonwealth-development-office.atom |
| NATO | https://www.nato.int/cps/en/natolive/news.htm (check for RSS) |
| UN News | https://news.un.org/feed/subscribe/en/news/all/rss.xml |
| UN Security Council | https://www.un.org/securitycouncil/content/resolutions (manual check) |
| ReliefWeb | https://reliefweb.int/updates/rss.xml |
| OSCE | https://www.osce.org/rss.xml |

### 8.3 Defense / Security News Feeds

| Source | Feed |
|--------|------|
| Janes | https://www.janes.com (check for RSS) |
| War on the Rocks | https://warontherocks.com/feed/ |
| Defence Blog | https://defence-blog.com/feed/ |
| DARPA | https://www.darpa.mil/RSS |
| National Defense Magazine | https://www.nationaldefensemagazine.org/rss |
| Army Technology | https://www.army-technology.com/feed/ |
| Naval Technology | https://www.naval-technology.com/feed/ |
| Air Force Technology | https://www.airforce-technology.com/feed/ |
| Military Embedded Systems | Check site for RSS |
| The Drive - War Zone | https://www.thedrive.com/the-war-zone/feed |

### 8.4 Threat Intelligence Feeds

| Source | Feed |
|--------|------|
| MISP default feeds | Built into MISP platform |
| CIRCL (Luxembourg CERT) | https://www.circl.lu (OSINT feeds) |
| US-CERT / CISA | https://www.cisa.gov/news-events/cybersecurity-advisories/rss |
| Krebs on Security | https://krebsonsecurity.com/feed/ |
| Schneier on Security | https://www.schneier.com/feed/ |
| BleepingComputer | https://www.bleepingcomputer.com/feed/ |

---

## 9. INTEGRATION PRIORITY MATRIX

### Tier 1: Free, High-Value, Easy Integration (start here)
| Source | Why |
|--------|-----|
| **GDELT** | Unlimited free event data, 15-min updates, global coverage |
| **Bluesky Jetstream** | Free real-time social firehose, no auth needed |
| **ReliefWeb API** | Free humanitarian crisis data, well-documented JSON API |
| **USGS Earthquake API** | Free, no auth, real-time global seismic data |
| **NASA EONET** | Free curated natural disaster events with satellite imagery links |
| **NASA FIRMS** | Free near-real-time global fire detection |
| **WHO Disease Outbreak API** | Free disease outbreak monitoring |
| **OFAC SLS** | Free sanctions list with API |
| **AlienVault OTX** | Most generous free threat intel platform |
| **RSS feeds** | Zero cost, broad coverage, easy to aggregate |
| **ACLED** | Free conflict event data with registration |
| **UCDP** | Free conflict data, including near-real-time candidate events |
| **Copernicus (CDS/CEMS/Sentinel)** | Free EU satellite + climate + emergency data |

### Tier 2: Free but Limited (useful supplements)
| Source | Limitation |
|--------|-----------|
| **NewsAPI.org** | 100 req/day, 24h delay |
| **Newsdata.io** | 200 credits/day, 12h delay |
| **World News API** | 500 req/day |
| **Currents API** | 600 req/day |
| **GNews** | 100 req/day |
| **Reddit API** | 100 QPM but requires OAuth, non-commercial |
| **OpenSky Network** | 4,000 credits/day, non-commercial |
| **VirusTotal** | 500 req/day, 4/min |
| **AbuseIPDB** | 1,000 req/day |
| **GreyNoise Community** | 1 endpoint, basic data only |
| **Shodan** | 50 queries/month free |
| **Mastodon** | Requires auth, per-instance connection |
| **OpenSanctions** | Free non-commercial only |

### Tier 3: Paid but Worth Considering
| Source | Starting Price |
|--------|---------------|
| **FlightRadar24 API** | $9/month |
| **Newscatcher** | $29/month |
| **MediaStack** | $24.99/month |
| **World News API (paid)** | $35/month |
| **NewsAPI.org (paid)** | $40/month |
| **Newsdata.io (paid)** | $199.99/month |
| **MarineTraffic** | Enterprise pricing |
| **Shodan Membership** | $49 one-time |

---

## 10. RECOMMENDED ARCHITECTURE NOTES

### News Aggregation Strategy
Combine multiple free news APIs to overcome individual rate limits:
1. Use GDELT as the primary high-volume event backbone (unlimited, 15-min updates)
2. Rotate between NewsAPI.org, GNews, Currents, World News API for article enrichment
3. Use RSS feeds for specialized sources (defense, government, security)
4. Add Newsdata.io for broad country/language coverage

### Social Media Strategy
1. Bluesky Jetstream as always-on social firehose (free, real-time)
2. Mastodon streaming for fediverse coverage (requires per-instance auth)
3. Reddit polling for specific subreddits (r/worldnews, r/geopolitics, etc.)
4. Telegram channel monitoring via Telethon for conflict zones and OSINT channels

### Threat Intel Strategy
1. AlienVault OTX as primary free threat intel source
2. abuse.ch feeds for malware/botnet IOCs
3. VirusTotal for file/URL scanning (within daily limits)
4. AbuseIPDB for IP reputation checks
5. MISP for feed aggregation and correlation

### Geopolitical Event Strategy
1. GDELT for high-volume automated event coding
2. ACLED for curated conflict/protest data
3. UCDP Candidate Events for near-real-time conflict monitoring
4. ReliefWeb for humanitarian situation reports

### Environmental/Disaster Strategy
1. USGS for earthquakes
2. NASA EONET for curated multi-hazard events
3. NASA FIRMS for fires
4. Copernicus CEMS for floods/drought
5. NOAA for climate/weather
