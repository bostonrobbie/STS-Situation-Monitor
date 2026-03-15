"""
osint_research.py — Automated OSINT collection and research synthesis.
Triggered when a Developing Situation is detected.
"""
from __future__ import annotations
import hashlib
import json
import re
from datetime import UTC, datetime

import httpx


def _google_news_query(query: str, max_items: int = 10) -> list[dict]:
    """Fetch Google News RSS for a query, return list of {title, url, source, date}."""
    url = f"https://news.google.com/rss/search?q={query.replace(' ','+')}&hl=en-US&gl=US&ceid=US:en"
    results = []
    try:
        r = httpx.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code != 200:
            return results
        items = re.findall(r'<item>(.*?)</item>', r.text, re.DOTALL)
        for item in items[:max_items]:
            title_m = re.search(r'<title>(.*?)</title>', item, re.DOTALL)
            link_m = re.search(r'<link>(.*?)</link>', item, re.DOTALL)
            source_m = re.search(r'<source[^>]*>(.*?)</source>', item, re.DOTALL)
            if not title_m:
                continue
            title = re.sub(r'<[^>]+>', '', title_m.group(1)).strip()
            link = link_m.group(1).strip() if link_m else ''
            source = re.sub(r'<[^>]+>', '', source_m.group(1)).strip() if source_m else 'Google News'
            results.append({'title': title, 'url': link, 'source': source})
    except Exception:
        pass
    return results


def extract_keywords(text: str, max_keywords: int = 5) -> list[str]:
    """Extract key noun phrases from text for use as search queries."""
    # Simple approach: extract capitalized multi-word phrases and proper nouns
    words = re.findall(r'\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b', text)
    # Deduplicate and take top ones by length
    seen = set()
    keywords = []
    for w in words:
        if w.lower() not in seen and len(w) > 3:
            seen.add(w.lower())
            keywords.append(w)
            if len(keywords) >= max_keywords:
                break
    return keywords


def run_research_plan(situation_title: str, situation_summary: str,
                      center_lat: float, center_lon: float,
                      ollama_url: str = "http://localhost:11434") -> dict:
    """
    Auto-research pipeline:
    1. Extract keywords from situation
    2. Run targeted Google News queries
    3. Ask LLM to synthesize findings into a dossier
    Returns dict with findings, sources, dossier
    """
    keywords = extract_keywords(situation_title + ' ' + situation_summary)
    all_articles: list[dict] = []
    queries_run: list[str] = []

    # Run up to 3 queries
    for kw in keywords[:3]:
        articles = _google_news_query(kw, max_items=8)
        all_articles.extend(articles)
        queries_run.append(kw)

    # Deduplicate by title hash
    seen_hashes = set()
    unique_articles = []
    for a in all_articles:
        h = hashlib.md5(a['title'].encode()).hexdigest()[:8]
        if h not in seen_hashes:
            seen_hashes.add(h)
            unique_articles.append(a)

    # LLM synthesis
    dossier = ''
    if unique_articles:
        headlines_text = '\n'.join([f"- [{a['source']}] {a['title']}" for a in unique_articles[:20]])
        prompt = f"""You are an intelligence analyst. A developing situation has been detected:

SITUATION: {situation_title}
LOCATION: {center_lat:.2f}°N, {center_lon:.2f}°E

COLLECTED NEWS HEADLINES:
{headlines_text}

Based on these sources, provide a concise intelligence dossier answering:
1. WHAT IS HAPPENING: (2-3 sentences)
2. KEY ACTORS INVOLVED: (bullet points)
3. VERIFIED FACTS: (what multiple sources confirm)
4. DISPUTED/UNCLEAR: (what is uncertain or contested)
5. SIGNIFICANCE: (why this matters, 1-2 sentences)

Be objective and cite sources where possible. Format with clear headers."""

        try:
            resp = httpx.post(
                f"{ollama_url}/api/generate",
                json={"model": "qwen2.5:14b", "prompt": prompt, "stream": False,
                      "options": {"temperature": 0.3, "num_predict": 600}},
                timeout=90.0,
            )
            if resp.status_code == 200:
                dossier = resp.json().get('response', '').strip()
        except Exception:
            dossier = f"Auto-research found {len(unique_articles)} articles. Manual review recommended."

    return {
        'situation_title': situation_title,
        'queries_run': queries_run,
        'articles_found': len(unique_articles),
        'sources': unique_articles,
        'dossier': dossier,
        'generated_at': datetime.now(UTC).isoformat(),
    }
