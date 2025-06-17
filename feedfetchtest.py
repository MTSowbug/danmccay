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
from models import SPEAKING_MODEL, THINKING_MODEL
import yaml
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


def _strip_html(text: str) -> str:
    """Return *text* with HTML tags removed."""
    return re.sub(r"<[^>]+>", "", text or "")


def _extract_doi(entry) -> str:
    """Return a DOI URL from *entry* if present."""
    doi = entry.get("dc_identifier") or entry.get("doi") or ""
    if isinstance(doi, str) and doi.lower().startswith("doi:"):
        doi = doi.split("doi:", 1)[1].strip()
    if not doi:
        # Look for a DOI pattern in id or link fields
        for field in (entry.get("id"), entry.get("link")):
            if field:
                m = re.search(r"10\.\d{4,9}/[-._;()/:A-Z0-9]+", str(field), re.I)
                if m:
                    doi = m.group(0)
                    break
    if doi and not doi.startswith("http"):
        doi = f"https://doi.org/{doi}"
    return doi


def _entry_to_article_data(entry) -> dict:
    """Return a dictionary with standardized article metadata for storage."""
    ts = _entry_timestamp(entry)
    authors = []
    if hasattr(entry, "authors"):
        authors = [a.get("name") for a in entry.authors if isinstance(a, dict)]
    elif hasattr(entry, "author"):
        authors = [entry.author]

    abstract = _strip_html(entry.get("summary") or entry.get("description") or "")

    return {
        "doi": _extract_doi(entry),
        "title": entry.get("title", ""),
        "authors": authors,
        "journal": entry.get("dc_source") or entry.get("source") or "",
        "link": entry.get("link", ""),
        "year": ts.year if ts else None,
        "abstract": abstract,
        "date-added": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "num-retrievals": 0,
        "lt-relevance": 0,
        "dr-relevance": 0,
        "sbir-relevance": 0,
    }


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
    sample_path = _BASE_DIR / "sample_pdf_fetch.sh"
    try:
        sample_script = sample_path.read_text(encoding="utf-8")
    except Exception:
        sample_script = ""

    print(f"Entry link:\n{entry.link}")

    messages = [
        {
            "role": "system",
            "content": (
                "You provide Linux shell commands for obtaining peer-reviewed scientific articles as PDFs."
            ),
        },
    ]

    if sample_script:
        messages.append(
            {
                "role": "system",
                "content": (
                    "Here is an example of a successful script you may use as a style reference:\n" +
                    "```bash\n" + sample_script + "\n```"
                ),
            }
        )

    messages.append(
        {
            "role": "user",
            "content": (
                """
Provide Linux shell commands to find and download the full text of the scientific article described at this URL: """ + entry.link + """.
This link is just a starting point - you will have to determine the URL of the article itself via web browsing. You may not have the correct institutional credentials - you will have to determine whether or not this is true. Provide commands to download the best version of the article available.
Search the web to find details you need about how the relevant website is structured in order to find the PDF.
Note that external arguments cannot be used with your shell commands.
Name the downloaded pdf "article_fulltest_version1.pdf". When you attempt to download multiple versions of the pdf, name them "article_fulltest_version2.pdf", "article_fulltest_version3.pdf", and so on. The best version of the article should be version1, 2nd-best version should be version2, and so on.
Do not attempt to end the script early via "exit" or other means; instead, download every version of the article that you can.
The shell commands may make use of an external file "../pdfs/jar.cookies" that contains various institutional credentials.
Include extensive debugging information by the use of echos, such that, if your code fails, you will be able to learn what went wrong in the future.
Respond only with shell commands or a shell script that can be directly pasted into a terminal. Type nothing else.
                """
            ),
        }
    )

    try:
        resp = client.chat.completions.create(
            model=THINKING_MODEL,
            messages=messages,
            max_completion_tokens=3000,
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
            executable="/bin/bash",  # generated script often relies on bash
        )
        output = result.stdout + result.stderr
        if output:
            print(f"Script output:\n{output}")
    except Exception as exc:
        print(f"Script execution failed: {exc}")
        result = None
        output = ""

    if result and result.returncode != 0:
        try:
            print("Script failed, requesting troubleshooting suggestions...")
            retry_messages = messages + [
                {
                    "role": "user",
                    "content": (
                        "The previous shell script failed with exit code "
                        f"{result.returncode} and output:\n{output}\n"
                        "Please provide a corrected script to accomplish the"
                        " same task. Respond only with the script."
                    ),
                }
            ]
            retry = client.chat.completions.create(
                model=THINKING_MODEL,
                messages=retry_messages,
                max_completion_tokens=3000,
            )
            retry_script = _extract_shell_script(
                retry.choices[0].message.content.strip()
            )
            print(f"LLM provided retry script:\n{retry_script}")
            result2 = subprocess.run(
                retry_script,
                shell=True,
                cwd=dest_dir,
                capture_output=True,
                text=True,
                executable="/bin/bash",
            )
            output2 = result2.stdout + result2.stderr
            if output2:
                print(f"Retry script output:\n{output2}")
            output += "\n" + output2
        except Exception as exc:
            print(f"Retry attempt failed: {exc}")

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


def _extract_doi_from_pdf(path: Path) -> str:
    """Return a DOI URL if one can be parsed from *path*."""
    try:
        from PyPDF2 import PdfReader

        reader = PdfReader(str(path))
        text = ""
        for page in reader.pages[:2]:
            try:
                text += page.extract_text() or ""
            except Exception:
                continue
        m = re.search(r"https?://doi.org/\S+", text)
        if m:
            return m.group(0)
    except Exception as exc:
        print(f"DOI extraction from PDF failed: {exc}")
    return ""


def _extract_doi_from_url(url: str) -> str:
    """Return a DOI URL discovered on *url* or via redirects."""
    if not url:
        return ""
    try:
        with urllib.request.urlopen(url) as resp:
            final = resp.geturl()
            data = resp.read().decode("utf-8", errors="ignore")
    except Exception as exc:
        print(f"Failed to fetch {url}: {exc}")
        return ""
    if final.startswith("https://doi.org/"):
        return final

    m = re.search(r"https://doi.org/10\.[^'\"\s<>]+", data)
    if m:
        return m.group(0)

    m = re.search(
        r"citation_doi[^>]+content=[\'\"](10\.[^\'\"]+)[\'\"]", data, re.I
    )
    if m:
        return f"https://doi.org/{m.group(1)}"

    m = re.search(r"doi:?\s*(10\.[^\'\"\s<>]+)", data, re.I)
    if m:
        return f"https://doi.org/{m.group(1)}"

    return ""


def _extract_journal_from_url(url: str) -> str:
    """Return a journal title discovered on *url*."""
    if not url:
        return ""
    try:
        with urllib.request.urlopen(url) as resp:
            data = resp.read().decode("utf-8", errors="ignore")
    except Exception as exc:
        print(f"Failed to fetch {url}: {exc}")
        return ""

    m = re.search(r"citation_journal_title[^>]+content=['\"]([^'\"]+)['\"]", data, re.I)
    if m:
        return m.group(1)

    m = re.search(r"property=['\"]og:site_name['\"] content=['\"]([^'\"]+)['\"]", data, re.I)
    if m:
        return m.group(1)

    return ""


def _llm_extract_doi(html: str) -> str:
    """Use an LLM to guess the DOI URL in *html*."""
    snippet = html[:8000]
    client = openai.OpenAI()
    messages = [
        {
            "role": "system",
            "content": (
                "Extract the doi.org URL for the scientific article referenced in"
                " this Fight Aging! blog post. Respond only with that URL."
            ),
        },
        {"role": "user", "content": snippet},
    ]

    try:
        resp = client.chat.completions.create(
            model=THINKING_MODEL,
            messages=messages,
            max_completion_tokens=30,
        )
        text = resp.choices[0].message.content
    except Exception as exc:
        print(f"LLM DOI extraction failed: {exc}")
        return ""

    m = re.search(r"https?://doi.org/[^\s]+", text)
    return m.group(0).strip() if m else ""


def _resolve_fightaging_item(url: str) -> tuple[str, str, str]:
    """Return the actual article link, DOI, and journal from a Fight Aging! post."""
    try:
        with urllib.request.urlopen(url) as resp:
            html = resp.read().decode("utf-8", errors="ignore")
    except Exception as exc:
        print(f"Failed to fetch {url}: {exc}")
        return url, "", ""

    doi = _llm_extract_doi(html)
    if doi:
        target = doi
    else:
        m = re.search(r"href=\"(https?://(?!www\.fightaging\.org)[^\"]+)\"", html)
        target = m.group(1) if m else url
        doi = _extract_doi_from_url(target)

    journal = _extract_journal_from_url(target)
    return target, doi, journal


def _discover_doi(entry, pdf_path: Path | None = None) -> str:
    """Attempt to retrieve the DOI URL for *entry* or *pdf_path*."""
    doi = _extract_doi_from_url(getattr(entry, "link", ""))
    if not doi and pdf_path is not None:
        doi = _extract_doi_from_pdf(pdf_path)
    return doi


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
        Keys are stable article IDs; values contain standardized article metadata.
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
            link = entry.get("link", "")
            if "fightaging.org" in urllib.parse.urlparse(link).netloc:
                new_link, doi, journal = _resolve_fightaging_item(link)
                entry["link"] = new_link
                entry.link = new_link
                if doi:
                    entry["doi"] = doi
                if journal:
                    entry["dc_source"] = journal

            article = _entry_to_article_data(entry)
            articles[key] = article
            if download_pdfs:
                pdf_path = _download_pdf(entry, _PDF_DIR)
                if pdf_path:
                    rel = pdf_path.relative_to(_PDF_DIR)
                    articles[key]["pdf"] = str(rel)
                    doi = _discover_doi(entry, pdf_path)
                    if doi:
                        articles[key]["doi"] = doi
                time.sleep(random.uniform(5, 10))
            print(entry.title)

    if json_path is not None:
        _save_articles(articles, Path(json_path))

    return articles


def download_missing_pdfs(
    json_path: Path = _ARTICLES_JSON,
    max_articles: int | None = None,
) -> None:
    """Download PDFs for entries in *json_path* that lack them.

    Parameters
    ----------
    json_path : Path
        Location of the ``articles.json`` file.
    max_articles : int or None, optional
        If given, limit the number of PDFs fetched to at most this many.
    """
    if not json_path.is_file():
        print(f"JSON file not found: {json_path}")
        return

    with json_path.open("r", encoding="utf-8") as fh:
        articles = json.load(fh)

    updated = False
    processed = 0
    for key, data in articles.items():
        if data.get("pdf"):
            continue
        if max_articles is not None and processed >= max_articles:
            break

        class Entry:
            pass

        entry = Entry()
        entry.title = data.get("title", "")
        link = data.get("link") or key
        if not link:
            doi = data.get("doi")
            if doi:
                link = doi
        entry.link = link

        pdf_path = _download_pdf(entry, _PDF_DIR)
        if pdf_path:
            rel = pdf_path.relative_to(_PDF_DIR)
            data["pdf"] = str(rel)
            doi = _discover_doi(entry, pdf_path)
            if doi:
                data["doi"] = doi
            updated = True
        processed += 1
        time.sleep(random.uniform(5, 10))

    if updated:
        _save_articles(articles, json_path)


def summarize_articles(
    json_path: Path = _ARTICLES_JSON,
    model: str = SPEAKING_MODEL,
    char_file: Path | str = (_BASE_DIR / "danmccay.yaml"),
) -> str:
    """Return an LLM-generated summary of all articles in *json_path*.

    The *char_file* YAML is loaded so the ``PAPERS`` section can be filled with
    an itemised list of papers before sending the prompt to the LLM.
    """
    if not Path(json_path).is_file():
        print(f"JSON file not found: {json_path}")
        return ""

    with Path(json_path).open("r", encoding="utf-8") as fh:
        try:
            articles = json.load(fh)
        except Exception as exc:
            print(f"Failed to load JSON: {exc}")
            return ""

    text_chunks = []
    for data in articles.values():
        title = data.get("title", "").strip()
        abstract = data.get("abstract", "").strip()
        if title or abstract:
            text_chunks.append((title, abstract))

    if not text_chunks:
        print("No articles available for summarization.")
        return ""

    # Build the PAPERS section for the character prompt
    papers_lines = ["PAPERS"]
    for idx, (title, abstract) in enumerate(text_chunks, 1):
        title = title or "(no title)"
        abstract = abstract or "(no abstract)"
        papers_lines.append(f"{idx}. {title} — {abstract}")
    papers_lines.append("******")
    papers_text = "\n".join(papers_lines)

    # Load the base character prompt and inject the papers list
    try:
        with Path(char_file).open("r", encoding="utf-8") as fh:
            core = yaml.safe_load(fh)
        char_section = core.get("prompts", {}).get("char", {})
        if isinstance(char_section, dict):
            parts = [
                char_section.get("system", ""),
                char_section.get("rules", ""),
                char_section.get("personality", ""),
                char_section.get("background", ""),
                papers_text,
            ]
            char_prompt = "\n".join(parts).strip()
        else:
            char_prompt = char_section
    except Exception as exc:
        print(f"Failed to load char prompt: {exc}")
        char_prompt = papers_text

    client = openai.OpenAI()
    messages = [
        {"role": "system", "content": char_prompt},
        {"role": "user", "content": "Provide a short summary and discussion."},
    ]

    try:
        resp = client.chat.completions.create(
            model=model,
            messages=messages,
            #max_tokens=500,
            max_completion_tokens=5000,
        )
        summary = resp.choices[0].message.content
    except Exception as exc:
        print(f"LLM request failed: {exc}")
        summary = ""

    return summary


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
