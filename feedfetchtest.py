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

import re
import urllib.parse
import urllib.request

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


def _sanitize_filename(name: str) -> str:
    """Return *name* stripped to a safe filesystem format."""
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", name)
    return safe[:50]


def _download_pdf(entry, dest_dir: Path) -> Path | None:
    """Try to download a PDF for *entry* into *dest_dir*."""
    pdf_url = None
    for link in getattr(entry, "links", []):
        if "pdf" in link.get("type", "").lower():
            pdf_url = link.get("href")
            break
    if not pdf_url:
        try:
            with urllib.request.urlopen(entry.link) as resp:
                html = resp.read().decode("utf-8", "ignore")
            m = re.search(r"href=[\'\"](.*?\.pdf)[\'\"]", html, re.I)
            if m:
                pdf_url = urllib.parse.urljoin(entry.link, m.group(1))
        except Exception as e:
            print(f"Could not inspect page for PDF: {e}")
            return None
    if not pdf_url:
        return None

    dest_dir.mkdir(exist_ok=True)
    filename = _sanitize_filename(entry.title) + ".pdf"
    dest = dest_dir / filename
    try:
        urllib.request.urlretrieve(pdf_url, dest)
        print(f"Downloaded PDF {dest}")
        return dest
    except Exception as e:
        print(f"Failed to download PDF {pdf_url}: {e}")
        return None


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
            pdf_path = _download_pdf(entry, Path("pdfs"))
            if pdf_path:
                articles[key]["pdf"] = str(pdf_path)
            print(entry.title)

    return articles


if __name__ == "__main__":
    recent_articles = fetch_recent_articles("mccayfeeds.opml", hours=24)
    print(recent_articles)
