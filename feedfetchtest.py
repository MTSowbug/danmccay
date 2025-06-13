"""
Fetch every article published in the last 24 h from an OPML list of RSS feeds.

Dependencies
------------
pip install feedparser

Usage
-----
recent = fetch_recent_articles("feeds.opml", hours=24)
print(recent)
"""

from __future__ import annotations

import datetime as _dt
import xml.etree.ElementTree as _ET
from pathlib import Path
from typing import Dict, List

import feedparser as _fp


def _extract_feed_urls(opml_source: str | Path) -> List[str]:
    """Return all RSS `xmlUrl` values contained in an OPML document."""
    if isinstance(opml_source, Path) or Path(opml_source).is_file():
        xml = Path(opml_source).read_text(encoding="utf-8")
    else:
        xml = opml_source
    tree = _ET.fromstring(xml)
    return [
        node.attrib["xmlUrl"]
        for node in tree.iter("outline")
        if node.attrib.get("type") == "rss"
    ]


def _entry_timestamp(entry) -> _dt.datetime | None:
    """Normalize the best available timestamp on a feed entry to UTC."""
    ts = getattr(entry, "published_parsed", None) or getattr(
        entry, "updated_parsed", None
    )
    if ts is None:
        return None
    return _dt.datetime(*ts[:6], tzinfo=_dt.timezone.utc)


def fetch_recent_articles(
    opml_source: str | Path,
    hours: int = 24,
) -> Dict[str, dict]:
    """
    Collect every article newer than *hours* from all feeds in *opml_source*.

    Returns
    -------
    articles : dict
        Keys are stable article IDs; values hold title, link, timestamp, and feed.
    """
    cutoff = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(hours=hours)
    articles: Dict[str, dict] = {}

    print("Fetching...")
    for feed_url in _extract_feed_urls(opml_source):
        parsed = _fp.parse(feed_url)
        print(feed_url)

        for entry in parsed.entries:
            ts = _entry_timestamp(entry)
            if ts is None or ts < cutoff:
                continue

            # Compose a unique, deterministic key
            key = f"{entry.get('id', entry.link)}"
            articles[key] = {
                "title": entry.title,
                "link": entry.link,
                "published": ts.isoformat(),
                "feed": parsed.feed.get("title", feed_url),
            }
            print(entry.title)

    return articles


if __name__ == "__main__":
    recent_articles = fetch_recent_articles("mccayfeeds.opml", hours=24)
    print(recent_articles)
