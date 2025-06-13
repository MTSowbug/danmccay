"""
Fetch every article published in the last 24 h from an OPML list of RSS feeds.

Dependencies
------------
pip install feedparser openai

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
import json

import re
import urllib.parse
import urllib.request

import openai
import subprocess
import shlex
import time
import random

import feedparser as _fp

_BASE_DIR = Path(__file__).resolve().parent
_PDF_DIR = (_BASE_DIR / "../pdfs").resolve()
_ARTICLES_JSON = _PDF_DIR / "articles.json"

# Ensure a default articles store exists for convenience
_PDF_DIR.mkdir(parents=True, exist_ok=True)
if not _ARTICLES_JSON.is_file():
    _ARTICLES_JSON.write_text("{}", encoding="utf-8")


def _entry_to_jsonable(entry) -> dict:
    """Return *entry* converted to JSON-friendly types."""
    return json.loads(json.dumps(dict(entry), default=str))


def _save_articles(articles: Dict[str, dict], output_path: Path) -> None:
    """Write *articles* to *output_path*, merging with any existing data."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    existing: Dict[str, dict] = {}
    if output_path.is_file():
        with output_path.open("r", encoding="utf-8") as fh:
            try:
                existing = json.load(fh)
            except Exception:
                existing = {}

    existing.update(articles)
    with output_path.open("w", encoding="utf-8") as fh:
        json.dump(existing, fh, indent=2, sort_keys=True)


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


def _extract_shell_script(text: str) -> str:
    """Return the bash script contained in *text*."""
    m = re.search(r"```(?:bash)?\n(.*?)```", text, re.S)
    if m:
        text = m.group(1)
    return text.strip()


def _llm_shell_commands(entry, dest_dir: Path) -> str:
    """Ask the LLM for a shell script to download *entry* and execute it."""
    client = openai.OpenAI()
    messages = [
        {
            "role": "system",
            "content": (
                "You provide Linux shell commands for obtaining peer-reviewed scientific articles as PDFs."
            ),
        },
        {
            "role": "user",
            "content": (
                """
Provide Linux shell commands to find and download the full text of the scientific article described at this URL: """ + entry.link + """. 
This link is just a starting point - you will have to determine the URL of the article itself via web browsing. You may not have the correct institutional credentials - you will have to determine whether or not this is true. Provide commands to download the best version of the article available.
Search the web to find details you need about how the relevant website is structured in order to find the PDF.
Name the downloaded pdf "article_fulltest_version1.pdf". If you attempt to download multiple versions of the pdf, name them "article_fulltest_version2.pdf", "article_fulltest_version3.pdf", and so on. The best version of the article should be version1, 2nd-best version should be version2, and so on.
The shell commands may make use of an external file "../pdfs/jar.cookies" that contains various institutional credentials.
Include extensive debugging information by the use of echos, such that, if your code fails, you will be able to learn what went wrong in the future.
Respond only with shell commands or a shell script that can be directly pasted into a terminal. Type nothing else.
                """
            ),
        },
    ]

    try:
        resp = client.chat.completions.create(
            model="gpt-4.1-2025-04-14",
            messages=messages,
            #max_tokens=3000,
            max_completion_tokens=3000,
            #temperature=0,
        )
    except Exception as exc:
        print(f"LLM request failed: {exc}")
        return ""

    script = _extract_shell_script(resp.choices[0].message.content.strip())
    print(f"LLM provided script:\n{script}")

    try:
        result = subprocess.run(
            script,
            shell=True,
            cwd=dest_dir,
            capture_output=True,
            text=True,
        )
        output = result.stdout + result.stderr
        if output:
            print(f"Script output:\n{output}")
    except Exception as exc:
        print(f"Script execution failed: {exc}")
        output = ""

    return output


def _is_safe_command(cmd: str) -> bool:
    """Check if *cmd* looks safe to execute."""
    if re.search(r"[;&|`$]", cmd):
        return False
    tokens = shlex.split(cmd)
    if not tokens:
        return False
    return tokens[0] in {"wget", "curl"}


def _pdf_file_valid(path: Path) -> bool:
    """Return ``True`` if *path* is plausibly a real article PDF."""
    if path.stat().st_size < 10_000:
        print(f"PDF too small: {path}")
        return False
    try:
        from PyPDF2 import PdfReader

        PdfReader(str(path))
    except Exception as exc:
        print(f"PDF corrupt: {path} ({exc})")
        return False
    return True


def _download_pdf(entry, dest_dir: Path) -> Path | None:
    """Try to download a PDF for *entry* into *dest_dir* using an LLM script."""
    dest_dir.mkdir(exist_ok=True)
    before = set(dest_dir.glob("*.pdf"))

    _llm_shell_commands(entry, dest_dir)

    after = set(dest_dir.glob("*.pdf"))
    new_files = sorted(after - before, key=lambda p: p.name)

    valid_pdfs = []
    for pdf in new_files:
        if _pdf_file_valid(pdf):
            valid_pdfs.append(pdf)
        else:
            pdf.unlink(missing_ok=True)

    if not valid_pdfs:
        return None

    chosen = valid_pdfs[0]
    for extra in valid_pdfs[1:]:
        extra.unlink(missing_ok=True)

    print(f"Downloaded PDF {chosen}")
    return chosen


def fetch_recent_articles(
    opml_source: str | Path,
    hours: int = 24,
    json_path: Path | None = _ARTICLES_JSON,
    download_pdfs: bool = True,
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
                "metadata": _entry_to_jsonable(entry),
            }
            if download_pdfs:
                pdf_path = _download_pdf(entry, _PDF_DIR)
                if pdf_path:
                    articles[key]["pdf"] = str(pdf_path)
                time.sleep(random.uniform(5, 10))
            print(entry.title)

    if json_path is not None:
        _save_articles(articles, Path(json_path))

    return articles


def download_missing_pdfs(
    json_path: Path = _ARTICLES_JSON,
) -> None:
    """Download PDFs for entries in *json_path* that lack them."""
    if not json_path.is_file():
        print(f"JSON file not found: {json_path}")
        return

    with json_path.open("r", encoding="utf-8") as fh:
        articles = json.load(fh)

    updated = False
    for data in articles.values():
        if data.get("pdf"):
            continue

        class Entry:
            pass

        entry = Entry()
        entry.title = data.get("title", "")
        entry.link = data.get("link", "")

        pdf_path = _download_pdf(entry, _PDF_DIR)
        if pdf_path:
            data["pdf"] = str(pdf_path)
            updated = True
        time.sleep(random.uniform(5, 10))

    if updated:
        _save_articles(articles, json_path)


if __name__ == "__main__":
    import sys

    args = [arg.lower() for arg in sys.argv[1:]]

    if any("rss" in arg for arg in args):
        fetch_recent_articles(
            "mccayfeeds.opml",
            hours=24,
            download_pdfs=False,
        )
    elif any("pdf" in arg for arg in args):
        download_missing_pdfs()
    else:
        print("Usage: python feedfetchtest.py [rss|pdf]")
