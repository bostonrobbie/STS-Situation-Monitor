from __future__ import annotations

import pytest

from sts_monitor.connectors.reddit import RedditConnector


pytestmark = pytest.mark.unit


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


class _FakeClient:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def get(self, url: str):
        _ = url
        return _FakeResponse(self.payload)


def test_reddit_connector_collects_posts(monkeypatch) -> None:
    payload = {
        "data": {
            "children": [
                {
                    "data": {
                        "title": "Power outage reported",
                        "selftext": "District 7 affected",
                        "permalink": "/r/news/comments/abc123/power_outage/",
                        "created_utc": 1710000000,
                    }
                }
            ]
        }
    }

    monkeypatch.setattr("sts_monitor.connectors.reddit.httpx.Client", lambda **_: _FakeClient(payload))

    connector = RedditConnector(subreddits=["news"], per_subreddit_limit=5)
    result = connector.collect(query="power")

    assert result.connector == "reddit"
    assert len(result.observations) == 1
    assert result.observations[0].source == "reddit:r/news"
    assert result.metadata["subreddit_count"] == 1
