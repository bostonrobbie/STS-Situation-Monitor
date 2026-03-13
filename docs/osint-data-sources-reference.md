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

## 10. LIVE CAMERAS, CCTV & VIDEO MONITORING

### 10.1 Public Webcam Aggregators

#### 10.1.1 Windy Webcams API (RECOMMENDED - Best Available API)
- **URL**: https://api.windy.com/webcams
- **API Docs**: https://api.windy.com/webcams/api/v3/docs
- **Auth**: API key via `x-windy-api-key` header (free registration)
- **Free tier**: Yes. Low-resolution images only. Image URL tokens expire after 10 minutes.
- **Professional tier**: No ads, unrestricted access, `all-webcams.json` bulk endpoint, 24-hour token expiry.
- **Data format**: JSON REST API; image URLs (JPEG snapshots)
- **Key endpoints (V3)**:
  - `GET /webcams/api/v3/webcams` — list/search webcams (supports geo bounding box, category, country, region filters)
  - `GET /webcams/api/v3/webcams/{webcamId}` — single webcam detail
  - `GET /webcams/api/v3/categories` — list categories
  - `GET /webcams/api/v3/continents|countries|regions` — geographic filters
- **Include params**: `?include=categories,images,location,player,urls`
- **Rate limits**: 1-second delay between requests. Cache with 5-minute TTL recommended.
- **Coverage**: Global (hundreds of thousands of webcams)
- **Migration note**: V2 is deprecated; use V3. Migration guide at https://api.windy.com/webcams/version-transfer
- **Legal**: Public API, legitimate OSINT use. Respect token expiry and rate limits.
- **Integration priority**: HIGH — this is the single best webcam API for programmatic integration.

#### 10.1.2 EarthCam
- **URL**: https://www.earthcam.com (consumer) / https://www.earthcam.net/api/ (enterprise API)
- **Auth**: Enterprise API requires direct contact with EarthCam for credentials
- **Free tier**: No public free API. Consumer website is free to view.
- **API capabilities** (enterprise only):
  - Real-time weather conditions per camera (temperature, wind, precipitation)
  - Computer vision / AI object detection (50+ object types, PPE detection)
  - Image archive queries (historical snapshots by date/camera)
- **Data format**: JSON (API); HLS/embedded player (consumer site)
- **Coverage**: Curated high-quality cameras at landmarks, construction sites, and destinations worldwide
- **Partner integrations**: Procore, Autodesk BIM 360, DroneDeploy
- **Legal**: Enterprise/B2B model. No self-serve developer access.
- **Integration priority**: LOW — requires enterprise relationship. Use Windy API instead for programmatic access.

#### 10.1.3 Insecam
- **URL**: https://www.insecam.org
- **Auth**: None (web scraping required)
- **Free tier**: Free to view (no API)
- **Data format**: Embedded MJPEG/RTSP streams from source cameras; click-through reveals source IP
- **Coverage**: ~73,000+ unsecured cameras globally (using default/weak credentials)
- **Geolocation accuracy**: Approximate only — coordinates point to ISP address, not physical camera location. Accuracy within a few hundred miles.
- **Legal status**: HIGHLY PROBLEMATIC. These are cameras with default/weak passwords, not intentionally public cameras. Accessing them may violate computer fraud/unauthorized access laws (CFAA in the US, Computer Misuse Act in the UK, etc.) depending on jurisdiction. Viewing the Insecam website itself is legal; directly connecting to discovered camera IPs raises serious legal questions.
- **Integration priority**: NOT RECOMMENDED for a legitimate OSINT tool. Document for awareness only.

#### 10.1.4 SkylineWebcams
- **URL**: https://www.skylinewebcams.com
- **Auth**: N/A (no API)
- **Free tier**: Free to view on website/app
- **Data format**: HLS embedded player (no documented API or direct stream URLs)
- **Coverage**: Curated HD cameras at tourist destinations, beaches, volcanoes, city centers (primarily Europe and Mediterranean)
- **Legal**: Consumer viewing platform. No developer API available.
- **Integration priority**: NONE — no programmatic access. Manual monitoring only.

#### 10.1.5 WebcamTaxi
- **URL**: https://www.webcamtaxi.com
- **Auth**: N/A (no API)
- **Free tier**: Free to view
- **Data format**: Embedded streams (YouTube embeds, HLS)
- **Coverage**: Curated global webcams organized by category (city, beach, nature, animals)
- **Legal**: Consumer viewing platform.
- **Integration priority**: NONE — no programmatic access.

#### 10.1.6 RapidAPI Webcam Collections
- **URL**: https://rapidapi.com/collection/webcam-apis
- **Auth**: RapidAPI key
- **Notes**: Aggregated third-party webcam APIs available through RapidAPI marketplace. Quality and reliability vary. Check individual API documentation for specifics.

---

### 10.2 Traffic Cameras

#### 10.2.1 Caltrans CCTV (California DOT) — RECOMMENDED
- **URL**: https://cwwp2.dot.ca.gov/documentation/cctv/cctv.htm
- **GIS data**: https://gisdata-caltrans.opendata.arcgis.com
- **Auth**: None required
- **Free tier**: Completely free
- **Data format**: JSON, CSV, XML, TXT (camera metadata); JPEG snapshots (camera images); some metro areas offer 30-second HLS streams
- **Refresh rate**: Image updates every 1-20 minutes (location-dependent). Metro areas may have continuous or 30-second streaming.
- **Coverage**: Entire California State Highway Network, organized by Caltrans District (1-12)
- **Fields**: Camera ID, location, highway, direction, status, image/stream URL
- **Usage restrictions**: Bulk streaming (10+ simultaneous streams) requires written agreement with Caltrans Traffic Operations.
- **Third-party tools**:
  - `a3r0id/california-live-cams` (GitHub) — Python modules for all CA DOT highway cams
  - `BlinkTagInc/caltrans-images` (GitHub) — HTTPS proxy for Caltrans CCTV images
- **Legal**: Public government data, free for OSINT use. Respect bulk streaming limits.
- **Integration priority**: HIGH — well-documented, free, no auth required.

#### 10.2.2 511NY (New York State DOT)
- **URL**: https://511ny.org/developers/help
- **Auth**: Developer API key (free registration)
- **Free tier**: Free
- **Data format**: JSON or XML (configurable via `format` param)
- **Endpoint**: `GET https://511ny.org/api/getcameras?key={key}&format={format}`
- **Rate limit**: 10 calls per 60 seconds
- **Coverage**: New York State highway cameras
- **Additional data**: Traffic speeds, incidents, roadwork
- **Legal**: Public government API.
- **Integration priority**: HIGH for NY coverage.

#### 10.2.3 Other State 511 Systems
Most US state DOTs operate 511 systems with similar REST APIs for camera data. All require free developer API keys.

| State | API Docs URL | Notes |
|-------|-------------|-------|
| **Utah (UDOT)** | https://prod-ut.ibi511.com/developers/doc | Cameras, road conditions, weather stations, message signs |
| **Idaho** | https://511.idaho.gov/developers/doc | Cameras, restrictions, weather stations |
| **Arizona** | https://www.az511.com/developers/doc | Cameras, message boards, events |
| **Georgia** | https://511ga.org/developers/doc | Cameras, message signs, EV charging stations |
| **SF Bay (California)** | https://511.org/open-data | Token-based, free. Must credit 511.org |
| **DC** | https://opendata.dc.gov (search "traffic camera") | ArcGIS-based open data |

**Common pattern**: All provide camera location metadata + snapshot image URLs via REST/JSON. Streaming video (RTSP/HLS) is generally NOT available through 511 APIs — snapshots only.

#### 10.2.4 TrafficLand (Largest US Aggregator)
- **URL**: Available via FirstNet developer portal (https://developer.firstnet.com/firstnet/apis-sdks/trafficland)
- **Auth**: API key / redistribution agreement
- **Coverage**: 25,000+ traffic cameras across 200+ US cities. Redistribution agreements with 50+ state DOTs.
- **Data format**: RTSP streams (h.264, 192k, 12-15fps) — one of the few sources offering actual RTSP video, not just snapshots
- **Legal**: Authorized aggregator with DOT redistribution agreements.
- **Integration priority**: MEDIUM — best option if you need actual video streams rather than snapshots. Requires agreement.

#### 10.2.5 INRIX Traffic Cameras
- **URL**: https://docs.inrix.com/traffic/trafficcameras/
- **Auth**: API key (commercial)
- **Data format**: JSON metadata + JPEG snapshots
- **Features**: Find cameras in area, get camera info, retrieve latest captured image
- **Legal**: Commercial API.

#### 10.2.6 TomTom Traffic API
- **URL**: https://developer.tomtom.com/traffic-api/documentation/tomtom-maps/product-information/introduction
- **Auth**: API key (free tier available)
- **Free tier**: Limited free transactions
- **Data format**: JSON, Protobuf (Intermediate API)
- **What it provides**: Traffic FLOW data (speeds, travel times, congestion), traffic INCIDENTS — NOT camera images
- **Refresh rate**: Updates every minute (Intermediate API)
- **Coverage**: Global
- **Note**: TomTom does not provide camera imagery — use for traffic flow/incident data overlay alongside camera sources.
- **Legal**: Commercial API with free tier.

#### 10.2.7 HERE Traffic API
- **URL**: https://www.here.com/docs/bundle/traffic-api-developer-guide-v7/page/README.html
- **Auth**: API key (free tier available)
- **Free tier**: 2,500-5,000 free transactions/month (depending on API group)
- **Paid**: $2.50-$5.00 per 1,000 transactions after free tier
- **Data format**: JSON
- **What it provides**: Traffic flow, incidents, speed data, congestion levels — NOT camera images
- **Rate limit**: ~5 concurrent requests/second on basic plan
- **Coverage**: Global
- **Note**: Like TomTom, HERE provides traffic data, not camera feeds. Use for data overlay.
- **Legal**: Commercial API with free tier.

#### 10.2.8 TfL (Transport for London) Traffic Cameras
- **URL**: https://api.tfl.gov.uk (search for JamCam endpoints)
- **Auth**: Free registration for API key
- **Free tier**: Free, no rate limits documented
- **Data format**: JPEG snapshots
- **Coverage**: London, UK
- **Legal**: Open data, free for OSINT use.

---

### 10.3 Weather & Aviation Cameras

#### 10.3.1 FAA WeatherCams
- **URL**: https://weathercams.faa.gov
- **Auth**: None for web access; no documented public API for programmatic access
- **Free tier**: Free to view
- **Data format**: JPEG snapshots (web interface)
- **Refresh rate**: Updated every 10 minutes
- **Coverage**: 600+ camera sites across North America (originally Alaska-focused, now 24+ US states)
- **Camera details**: Up to 4 cameras per site, each with directional reference and "clear day" comparison image
- **Impact**: 85% reduction in weather-related aviation accidents in Alaska (2007-2014)
- **Integration**: No public REST API found. Would require web scraping or ForeFlight integration.
- **Third-party**: ForeFlight integrates 500+ FAA WeatherCams (commercial aviation app)
- **Legal**: Government data, public viewing intended. Scraping legality varies.
- **Integration priority**: MEDIUM — valuable data but no clean API. Consider scraping or requesting API access from FAA.

#### 10.3.2 NOAA/NWS Weather Cameras
- **URL**: https://www.weather.gov/slc/Cameras (example — varies by NWS office)
- **Auth**: None
- **Notes**: NWS uses third-party webcams with permission. Coverage is inconsistent and varies by regional forecast office. No centralized API for camera feeds.
- **Legal**: Public government resource.
- **Integration priority**: LOW — fragmented, no centralized access.

#### 10.3.3 AVWX REST API (Aviation Weather Data)
- **URL**: https://info.avwx.rest
- **Auth**: API key
- **Free tier**: Basic METAR and TAF parsing is free
- **Data format**: JSON, XML, YAML
- **What it provides**: Textual aviation weather data (METARs, TAFs) — NOT camera imagery
- **Source**: Pulls from NOAA ADDS
- **Note**: Complement camera feeds with structured weather data from this API.

#### 10.3.4 Airport Webcams (Various)
- No centralized API. Individual airports may operate public webcams.
- **AirportWebcams.net**: Aggregator site, no API, web viewing only.
- **Integration priority**: LOW — manual curation required.

---

### 10.4 Satellite Imagery (Near-Real-Time)

#### 10.4.1 Copernicus Data Space Ecosystem + Sentinel Hub (RECOMMENDED - Free)
- **URL**: https://dataspace.copernicus.eu
- **API Docs**: https://documentation.dataspace.copernicus.eu/APIs.html
- **Auth**: Free registration required
- **Free tier**: Completely free with monthly quotas. All functionalities available. Quotas reset monthly.
  - Processing Units (PUs) allocated monthly for Sentinel Hub
  - 10,000 free openEO credits/month
  - Bandwidth and transfer limits apply
- **Paid**: Extended quotas via CREODIAS (https://creodias.eu/pricing/sh-pricing/) or ESA Network of Resources
- **APIs available**:
  - Sentinel Hub (rendering, statistics, batch processing)
  - STAC catalog
  - openEO (cloud processing)
  - OData / OpenSearch (product discovery and download)
  - S3 API (object storage access)
- **Data format**: GeoTIFF, JPEG, PNG (rendered); NetCDF, SAFE (raw products)
- **Satellites & refresh rates**:
  - **Sentinel-2** (optical, 10m resolution): ~5-day revisit
  - **Sentinel-1** (SAR radar, 5-20m): ~6-day revisit (works through clouds/night)
  - **Sentinel-3** (ocean/land, 300m): daily
  - **Sentinel-5P** (atmospheric): daily
- **Coverage**: Global
- **Best for**: Change detection, flood mapping, vegetation, air quality, post-disaster assessment
- **Legal**: EU open data policy. Free for all uses.
- **Integration priority**: HIGH — best free satellite imagery source with robust APIs.

#### 10.4.2 Google Earth Engine (GEE)
- **URL**: https://earthengine.google.com
- **API**: Python and JavaScript SDKs
- **Auth**: Google account + Earth Engine registration
- **Free tier**: Free for academic and research use. Commercial use now available (paid).
- **Data catalog**: 90+ petabytes, 1,000+ datasets, 50+ years of historical imagery, resolutions as fine as 1m/pixel
- **Includes**: Landsat, MODIS, Sentinel-1/2, NAIP, precipitation, SST, elevation, and many more
- **Processing**: Server-side analysis at planetary scale — no need to download data locally
- **Custom data**: Upload your own GeoTIFF/Shapefile for analysis
- **Legal**: Free for research/academic. Commercial requires paid plan.
- **Integration priority**: HIGH for analysis — unmatched processing power and data catalog.

#### 10.4.3 Planet Labs (PlanetScope)
- **URL**: https://www.planet.com
- **Auth**: API key (paid subscription)
- **Free tier**: No permanent free tier. 30-day trial via Sentinel Hub. Sandbox data available.
- **Constellation**: 180+ Dove CubeSats
- **Resolution**: 3-5m per pixel
- **Refresh rate**: DAILY global coverage (entire Earth imaged every day)
- **Data format**: GeoTIFF (4-band and 8-band available)
- **Access via Sentinel Hub**: PlanetScope data can be ordered/accessed through Sentinel Hub APIs
- **Pricing**: Institutional/enterprise — contact Planet for quotes
- **Best for**: Daily change detection, agriculture, supply chain monitoring
- **Legal**: Commercial. Institutional/academic programs may provide discounted or free access.
- **Integration priority**: MEDIUM — excellent data but costly. Consider for high-priority monitoring targets only.

#### 10.4.4 Maxar Open Data Program
- **URL**: https://www.maxar.com/open-data
- **Auth**: None for open data downloads; available in Google Earth Engine as community dataset
- **Free tier**: FREE for disaster response imagery (pre/post event high-resolution data)
- **Resolution**: Sub-meter (up to 30cm)
- **Data format**: Cloud-Optimized GeoTIFF (COG), Analysis-Ready Data (ARD)
- **Coverage**: Activated for specific natural disasters and humanitarian crises only
- **GEE access**: `ee.ImageCollection("projects/sat-io/open-datasets/MAXAR-OPENDATA/{event_name}")`
- **Limitations**: Only available for activated disaster events — NOT an always-on data source
- **Legal**: Open data license for humanitarian use.
- **Integration priority**: HIGH for disaster response, N/A for routine monitoring.

#### 10.4.5 USGS EarthExplorer / Landsat
- **URL**: https://earthexplorer.usgs.gov
- **Auth**: Free Earthdata Login account
- **Free tier**: Completely free
- **Data format**: GeoTIFF
- **Resolution**: 15-30m (Landsat 8/9)
- **Refresh rate**: 16-day revisit per satellite (8 days with both Landsat 8+9)
- **Coverage**: Global, continuous since 1972
- **Legal**: US government open data, completely free.

#### 10.4.6 Satellite Imagery Summary Table

| Source | Resolution | Revisit | Free? | API? | Best For |
|--------|-----------|---------|-------|------|----------|
| **Sentinel-2** (Copernicus) | 10m | 5 days | Yes | Yes | General monitoring, change detection |
| **Sentinel-1** (Copernicus) | 5-20m | 6 days | Yes | Yes | All-weather/night SAR monitoring |
| **Landsat 8/9** (USGS) | 15-30m | 8-16 days | Yes | Yes | Long-term change analysis |
| **PlanetScope** | 3-5m | Daily | No | Yes | Daily high-res monitoring |
| **Maxar Open Data** | <1m | Event-based | Yes* | GEE | Disaster response only |
| **Google Earth Engine** | Varies (1m-1km) | Varies | Research only | Yes | Analysis platform, not data source |

---

### 10.5 Live Streaming Aggregation

#### 10.5.1 YouTube Live — Geolocation Search (RECOMMENDED)
- **URL**: https://developers.google.com/youtube/v3 (YouTube Data API v3)
- **Auth**: Google API key (free from Google Cloud Console)
- **Free tier**: Generous daily quota (10,000 units/day default; search costs 100 units each = ~100 searches/day)
- **Geo-search endpoint**: `GET /youtube/v3/search`
  - `type=video`
  - `eventType=live` (live streams only)
  - `location={lat},{lng}`
  - `locationRadius={radius}` (max 1,000km)
  - `q={keyword}` (optional)
- **Data format**: JSON
- **Caveats**:
  - Only geo-tagged videos/streams appear in location search — a small percentage of all videos
  - If results are sparse, use keyword-based search with location name appended instead
  - Max radius is 1,000km
- **Tools**:
  - YouTube Geo Search Tool (official): https://github.com/youtube/geo-search-tool
  - YouTube GeoFind (third-party): https://mattw.io/youtube-geofind/location — web tool with map visualization and CSV export
  - Official Python sample: https://github.com/youtube/api-samples/blob/master/python/geolocation_search.py
- **Legal**: Official API, legitimate use. Respect YouTube ToS.
- **Integration priority**: HIGH — best available API for discovering live video streams by location.

#### 10.5.2 Twitch
- **URL**: https://dev.twitch.tv/docs/api/reference/#get-streams
- **Auth**: OAuth2 Client Credentials (free registration)
- **Free tier**: Free
- **Geo-search**: NOT SUPPORTED natively. Twitch API does not expose streamer location or support geo-based stream discovery.
- **Workarounds**:
  - Filter by `game_id` (e.g., "IRL" category) + language as rough geo proxy
  - **Streams Charts** (https://streamscharts.com/discovery): Third-party analytics with country-level audience geolocation for 100,000+ channels
  - **RealtimeIRL** (https://github.com/muxable/rtirl-twitch-map): Twitch extension where IRL streamers broadcast GPS location on map
- **Legal**: Official API, legitimate use.
- **Integration priority**: LOW — no native geo-discovery. Useful only if specific IRL streamers are known.

#### 10.5.3 Livestream.com
- **URL**: https://livestream.com (now owned by Vimeo)
- **API**: Deprecated / merged into Vimeo API
- **Notes**: Livestream was acquired by Vimeo. The standalone Livestream API is effectively defunct. Use Vimeo API (https://developer.vimeo.com) for any remaining Livestream-hosted content.
- **Integration priority**: NONE — deprecated platform.

---

### 10.6 Social Media Live Video

#### 10.6.1 TikTok Live
- **Official API**: https://developers.tiktok.com — Research Tools available to approved researchers only. No public geo-discovery endpoint.
- **Unofficial tools**:
  - **TikTokLive** (Python): https://pypi.org/project/TikTokLive/ — connect to known user's livestream, receive events (comments, gifts, etc.)
  - **TikTok-Live-Connector** (Node.js): https://github.com/zerodytrash/TikTok-Live-Connector — same concept, Node.js
  - **Euler Stream**: https://www.eulerstream.com/docs — commercial TikTok LIVE API
  - **TikAPI** (unofficial): https://tikapi.io — managed unofficial API with OAuth
  - **Apify TikTok LIVE Scraper**: Keyword-based search (not geo-based)
- **Geo-discovery**: NOT AVAILABLE on any platform (official or unofficial)
- **Legal**: Official Research API requires approval. Unofficial tools may violate TikTok ToS.
- **Integration priority**: LOW — no geo-discovery; requires knowing specific usernames.

#### 10.6.2 Instagram Live
- **Official API**: No public API for discovering or accessing Instagram Live streams
- **Instagram Map** (launched August 2025): In-app feature showing location-tagged content from followed accounts. NOT exposed via API.
- **Geo-discovery**: NOT AVAILABLE via any API
- **Legal**: Instagram API is highly restricted. Scraping violates Meta ToS.
- **Integration priority**: NONE.

#### 10.6.3 Facebook Live
- **Official API**: https://developers.facebook.com/docs/live-video-api
- **What it does**: Manage/create live broadcasts, query live videos for specific Pages/Groups — NOT discover streams by location
- **Geo-targeting**: Supports restricting audience by country when publishing, not for discovery
- **Facebook Live Map**: Deprecated feature, was never API-accessible
- **Geo-discovery**: NOT AVAILABLE
- **Legal**: API access requires Meta app review.
- **Integration priority**: NONE for discovery. Only useful if monitoring specific known Pages/Groups.

#### 10.6.4 Social Media Live Video Summary
**No major social media platform offers a public API for discovering live video streams by geographic location.** YouTube is the only platform with any geo-search capability for live streams, and even there, results are limited to the small subset of geo-tagged content.

---

### 10.7 Maritime / Port Cameras

#### 10.7.1 PTZtv Seaport Network
- **URL**: https://www.ptztv.com/portfolio/seaports/
- **Auth**: None (public web streams)
- **Free tier**: Free to view
- **Data format**: HLS embedded streams + "Port Fever" snapshot archive (1 image/minute, 24/7)
- **Key cameras**:
  - New York Harbor: https://www.nyharborwebcam.com (includes live VHF marine radio)
  - Port New York (Hudson River): https://www.portnywebcam.com
  - Port Miami: https://www.portmiamiwebcam.com
  - Juneau Harbor: https://www.juneauharborwebcam.com
- **Archive**: Port Fever site provides calendar-based access to hourly snapshot playback groups
- **Legal**: Intentionally public streams. Legitimate OSINT use.
- **Integration priority**: MEDIUM — no API, but consistent HD streams at major US ports. Snapshot archive enables historical review.

#### 10.7.2 Port of Los Angeles
- **URL**: https://portoflosangeles.org/news/livestream
- **Auth**: None
- **Free tier**: Free to view
- **Cameras**:
  - Camera 1 (San Pedro): Main Channel, USS Iowa, Everport Container Terminal
  - Camera 2 (Wilmington): East Basin, Wilmington Waterfront, container terminals
- **Data format**: HD HLS streams
- **Legal**: Public port authority streams.

#### 10.7.3 World Ship Society Webcam Links
- **URL**: https://worldshipny.com/webcam-links/
- **Notes**: Curated directory of maritime webcams worldwide. No API — link aggregation only.

#### 10.7.4 Cruising Earth Port Webcams
- **URL**: https://www.cruisingearth.com/port-webcams/
- **Notes**: Aggregated cruise terminal and port webcams globally. No API.

---

### 10.8 Environmental Monitoring Cameras

#### 10.8.1 ALERTWildfire / ALERTCalifornia (RECOMMENDED)
- **URL**: https://www.alertwildfire.org (multi-state) / https://cameras.alertcalifornia.org (California)
- **Operated by**: UC San Diego (ALERTCalifornia), University of Nevada Reno, University of Oregon
- **Auth**: Public web viewing; API access requires institutional credential (200+ authorized users from 50+ agencies)
- **Free tier**: Free public web viewing. Controlled camera access requires agency agreement.
- **Data format**: HD streaming video via web player; PTZ (pan-tilt-zoom) control for authorized users
- **Coverage**:
  - California: Extensive network (ALERTCalifornia)
  - Oregon: 200+ cameras via University of Oregon / ALERTWest platform
  - Nevada, Idaho, Washington, Montana, Colorado: Various coverage
- **Users**: BLM, US Forest Service, Oregon Department of Forestry, NOAA, state DOTs, American Red Cross
- **AI features**: ALERTCalifornia integrates wildfire smoke detection AI
- **Legal**: Public viewing is legitimate. PTZ camera control restricted to authorized agencies.
- **Integration priority**: HIGH for wildfire monitoring — unique, high-value environmental awareness network. Public web viewing can supplement satellite-based fire detection (NASA FIRMS).

#### 10.8.2 USGS Volcano Webcams
- **URL**: https://www.usgs.gov/programs/VHP/multimedia/webcams
- **Auth**: None
- **Free tier**: Completely free
- **Data format**: JPEG snapshots (web); YouTube Live streams for active eruptions
- **Key camera networks**:
  - **Hawaii Volcano Observatory (HVO)**: Multiple cameras on Kilauea (B1cam, B2cam, F1cam thermal, K2cam, KPcam, KWcam). Available at https://volcanoes.usgs.gov/observatories/hvo/cams/
  - **Alaska Volcano Observatory**: https://avo.alaska.edu/webcam/ (multiple Alaska volcanoes)
  - **Cascades Volcano Observatory**: Johnston Ridge camera on Mt. St. Helens
- **Thermal cameras**: Record heat signatures, effective through volcanic gas and at night
- **Historical archive**: Browse past images at https://volcview.wr.usgs.gov/ashcam-gui/webcam.html
- **YouTube**: USGS YouTube channel hosts live streams during active eruptions
- **Refresh rate**: Continuous (24/7 operation). Web snapshots refresh every few seconds to minutes.
- **Legal**: US government public data. Completely legitimate for OSINT.
- **Integration priority**: HIGH for volcanic activity monitoring. Supplement with USGS Volcano Hazards alerts API.

#### 10.8.3 NOAA/NWS Flood & River Cameras
- **URL**: Varies by NWS regional office
- **Notes**: Some NWS offices operate river gauge cameras or partner with local cameras for flood monitoring. No centralized API or camera directory.
- **Integration priority**: LOW — too fragmented for systematic integration. Use NOAA river gauge data APIs instead.

---

### 10.9 Open CCTV Discovery via IoT Search Engines

#### 10.9.1 Shodan (IoT/CCTV Search)
- **URL**: https://api.shodan.io
- **Auth**: API key
- **Free tier**: ~50 search queries/month (see Section 4.4 for full details)
- **Membership**: $49 one-time for 100 query credits/month
- **CCTV-relevant search filters**:
  - `port:554` — RTSP streams (Real-Time Streaming Protocol)
  - `port:8080 "WebcamXP"` — WebcamXP software
  - `"IP camera"` — generic IP camera search (2.2M+ results)
  - `"Server: yawcam"` — Yawcam software
  - `port:554 has_screenshot:true` — RTSP with preview screenshots
  - Combine with `country:`, `city:`, `org:` for geographic filtering
- **Data format**: JSON (API); RTSP/MJPEG/HTTP streams (from discovered devices)
- **Stream access patterns**: Common RTSP paths include `/live.sdp`, `/stream1`, `/h264/ch1/main/av_stream` — varies by manufacturer
- **Scale**: 2,284,701+ "IP camera" results indexed globally

#### 10.9.2 Censys
- **URL**: https://search.censys.io
- **Auth**: API key (free tier available)
- **Notes**: Often produces different results from Shodan. Good for cross-referencing.

#### 10.9.3 ZoomEye
- **URL**: https://www.zoomeye.org
- **Notes**: Chinese cyberspace search engine. Useful for Asia-Pacific device discovery.

#### 10.9.4 Binary Edge
- **URL**: https://www.binaryedge.io
- **Notes**: Cloud attack surface, IoT device detection.

#### 10.9.5 CRITICAL LEGAL NOTICE FOR OPEN CCTV/IoT SEARCH

**Accessing camera streams discovered through Shodan, Censys, ZoomEye, or similar IoT search engines raises serious legal and ethical issues:**

1. **Unauthorized access is illegal**: Connecting to a camera stream — even one with no password — may constitute unauthorized access under:
   - **US**: Computer Fraud and Abuse Act (CFAA), 18 U.S.C. 1030
   - **UK**: Computer Misuse Act 1990
   - **EU**: Directive 2013/40/EU on attacks against information systems
   - Most other jurisdictions have equivalent laws

2. **"No password" does not mean "public"**: A camera running with default credentials or no authentication is still private property. The owner's failure to secure it does not constitute consent to access.

3. **Shodan indexing is legal**: Shodan's scanning and indexing of publicly accessible ports is generally considered legal. USING that data to access camera streams is where legal risk begins.

4. **What IS legal**:
   - Searching Shodan for statistics and metadata about exposed devices (counts, geographic distribution, manufacturer analysis)
   - Accessing cameras that are INTENTIONALLY public (e.g., government traffic cams, tourist webcams, webcam aggregator sites)
   - Security research with explicit written permission from device owners

5. **What is NOT legal for this tool**:
   - Connecting to private CCTV systems found via Shodan
   - Accessing cameras with default/weak credentials
   - Monitoring private spaces (homes, businesses) without consent

**Recommendation for STS Situation Monitor**: Use Shodan metadata for STATISTICS only (e.g., "N cameras exposed in region X"). Do NOT build features that connect to arbitrary discovered cameras. Instead, integrate INTENTIONALLY public sources listed in this document (DOT cameras, webcam aggregators, port authority streams, etc.).

---

### 10.10 Conflict Zone & Crisis Cameras

**Note**: Accessing live camera feeds from active conflict zones raises safety, ethical, and legal concerns. Live feeds can endanger civilians, journalists, and aid workers by revealing positions and activities.

#### 10.10.1 Recommended Approaches (Legal & Ethical)
Instead of seeking direct CCTV access in conflict zones, use these legitimate sources:

1. **OSINT aggregation accounts**: Verified OSINT analysts on social media (Bluesky, Twitter/X, Telegram) who curate and verify conflict footage with appropriate operational security considerations
2. **News agency live feeds**: Reuters, AP, BBC, Al Jazeera operate managed live cameras in conflict areas with editorial oversight
3. **Satellite imagery** (Sections 10.4.1-10.4.5): Sentinel-1 SAR works through clouds/smoke/night; Maxar Open Data activates for humanitarian crises
4. **ACLED + UCDP** (Section 7): Structured conflict event data with geolocation
5. **ReliefWeb** (Section 3.1): Humanitarian situation reports
6. **Institute for the Study of War (ISW)**: https://www.understandingwar.org — daily conflict assessments with maps

#### 10.10.2 What NOT to Do
- Do not attempt to access CCTV networks in conflict zones (potential war crime implications, endangers people)
- Do not redistribute unverified conflict footage without considering operational security impacts
- Do not use Shodan/Censys to find cameras in conflict-affected countries

---

### 10.11 Camera/Video Integration Priority Matrix

#### Tier 1: Free, API-Accessible, High Value (integrate first)
| Source | Why |
|--------|-----|
| **Windy Webcams API** | Best webcam API: global, documented, free tier, geo-search |
| **YouTube Live Geo Search** | Only platform with geo-discovery of live streams |
| **Caltrans CCTV** | Free, no auth, JSON, covers California highways |
| **State 511 Systems** | Free, API key only, covers most US states |
| **Copernicus/Sentinel Hub** | Free satellite imagery with robust APIs |
| **USGS Volcano Webcams** | Free, public, critical environmental monitoring |
| **ALERTWildfire** (public view) | Critical wildfire situational awareness |
| **Google Earth Engine** | Free for research, planetary-scale analysis |

#### Tier 2: Free, No API (requires scraping or manual integration)
| Source | Limitation |
|--------|-----------|
| **FAA WeatherCams** | No API — web scraping needed |
| **PTZtv Port Cameras** | No API — HLS stream URLs, snapshot archive |
| **Port of Los Angeles** | No API — embedded HLS streams |
| **SkylineWebcams** | No API — embedded player only |
| **EarthCam** (consumer site) | No API — enterprise API requires B2B agreement |

#### Tier 3: Paid / Limited
| Source | Cost |
|--------|------|
| **Planet Labs** | Enterprise pricing (daily global imagery) |
| **TomTom Traffic API** | Free tier then $$/1000 calls (data, not cameras) |
| **HERE Traffic API** | Free tier then $$/1000 calls (data, not cameras) |
| **TrafficLand** | Agreement required (25,000+ US traffic cameras, RTSP) |
| **INRIX Cameras** | Commercial API |
| **EarthCam API** | Enterprise B2B only |
| **Shodan** | $49 one-time for useful query volume |

### 10.12 Video/Camera Architecture Notes

#### Recommended Integration Strategy

1. **Webcam backbone**: Windy Webcams API as primary global webcam data source. Geo-search for cameras near areas of interest, cache metadata, refresh image tokens per API rules (every 10 minutes on free tier).

2. **Traffic layer**: Integrate state 511 APIs (start with Caltrans + 511NY) for US highway camera coverage. Use TomTom/HERE for traffic flow data overlay (not cameras).

3. **Live stream discovery**: YouTube Data API v3 geo-search with `eventType=live` for real-time citizen footage near incidents.

4. **Satellite layer**: Copernicus Sentinel Hub for change detection and post-event analysis. NASA FIRMS for real-time fire overlay. Maxar Open Data for disaster response.

5. **Environmental monitoring**: ALERTWildfire for fire watch, USGS volcano webcams for volcanic activity.

6. **Maritime**: PTZtv port cameras (manual embed/scrape) for major US ports.

#### Data Format Handling
Build the ingest pipeline to handle these common camera feed formats:
- **JPEG snapshots** (most common): Poll URL at interval, store as time-series images
- **HLS** (HTTP Live Streaming): `.m3u8` playlist URLs; use `ffmpeg` or `hls.js` to consume
- **MJPEG** (Motion JPEG): HTTP multipart stream; parse boundary frames
- **RTSP** (Real-Time Streaming Protocol): Use `ffmpeg` or `GStreamer` to capture; convert to HLS for web display
- **Embedded players**: Extract stream URLs from page source or use headless browser

#### Dashboard Display Approach
- Plot camera locations on map (Leaflet/MapLibre) using Windy + 511 + other geo-tagged sources
- Click to view snapshot or open stream
- Overlay satellite imagery tiles from Sentinel Hub WMS/WMTS endpoints
- Overlay traffic flow data from TomTom/HERE as colored road segments
- Time-slider for satellite imagery temporal analysis

---

## 11. RECOMMENDED ARCHITECTURE NOTES

### Visual/Camera Monitoring Strategy
1. Windy Webcams API as global webcam backbone (free, geo-searchable)
2. State 511 APIs for US highway camera network (free, JSON)
3. YouTube Live geo-search for citizen live streams near incidents
4. Copernicus Sentinel Hub for satellite change detection (free)
5. ALERTWildfire + USGS volcano cams for environmental monitoring
6. TomTom/HERE for traffic flow data overlay (not camera feeds)
7. PTZtv port cameras for maritime situational awareness

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
6. ALERTWildfire cameras for visual fire monitoring
7. USGS volcano webcams for eruption monitoring
8. Copernicus Sentinel-1 SAR for all-weather change detection
9. Maxar Open Data for high-res disaster imagery (activated per event)
