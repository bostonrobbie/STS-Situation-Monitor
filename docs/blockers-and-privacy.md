# Common blockers: offline LLM + live news/social feeds

This document maps practical blockers and mitigations for running STS as a private, mostly free, local-first stack.

## 1) Offline model vs internet ingestion

**Blocker:** An offline model cannot fetch live feeds directly.

**Mitigation:** Split architecture into:
- Online collectors (RSS/Reddit/etc.)
- Local database/object store
- Offline LLM reasoning worker over stored evidence only

## 2) API and policy limitations

- X/Twitter and many social platforms have restrictive API plans/rate limits.
- Some websites prohibit scraping in their terms.
- Geo and media-heavy sources can change markup frequently.

**Mitigation:** prioritize legal, stable connectors (RSS + select Reddit + official APIs), and include retries/backoff.

## 3) Trust and misinformation

**Blocker:** Raw feed aggregation increases false/contradictory claims.

**Mitigation:** enforce provenance for every output line:
- source URL
- capture timestamp
- claim-evidence links
- confidence with rationale

## 4) Cost control (free-first)

- Run local SQLite or Postgres on your machine.
- Use local LLM endpoints (localhost).
- Keep vector search optional until needed.

## 5) Privacy controls

- Local-only API binding whenever possible.
- Encrypt host disk where DB/files live.
- Keep secrets in environment variables, not source control.
- Restrict any file-browser features to an allowlisted workspace path.

## 6) Dashboard safety boundaries

A dashboard should not browse arbitrary system paths by default.
Use:
- read-only file explorer
- explicit allowlisted root folder
- import workflow for external files
- role-based access for write/delete operations
