"""
Fetch every article published in the last 24 h from an OPML list of RSS feeds.

Dependencies
------------
pip install feedparser openai PyPDF2

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
import shutil
import tempfile
import zipfile

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


def _extract_feed_urls(opml_source: str | Path, with_titles: bool = False) -> List:
    """Return RSS ``xmlUrl`` values (and optionally titles) from an OPML document."""
    if isinstance(opml_source, Path) or Path(opml_source).is_file():
        xml = Path(opml_source).read_text(encoding="utf-8")
    else:
        xml = opml_source
    tree = _ET.fromstring(xml)
    feeds = [
        (
            node.attrib["xmlUrl"],
            node.attrib.get("title") or node.attrib.get("text") or "",
        )
        for node in tree.iter("outline")
        if node.attrib.get("type") == "rss"
    ]
    if with_titles:
        return feeds
    return [url for url, _ in feeds]


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


def _parse_longevity_summary(summary: str) -> tuple[list[str], str, str]:
    """Extract authors, journal and abstract from a Longevity Papers summary."""
    authors: list[str] = []
    journal = ""
    abstract = ""
    if not summary:
        return authors, journal, abstract

    m = re.search(r"<strong>Authors:</strong>(.*?)</p>", summary, re.S)
    if m:
        names = _strip_html(m.group(1)).split(",")
        authors = [n.strip() for n in names if n.strip()]

    m = re.search(r"<strong>Journal:</strong>(.*?)</p>", summary, re.S)
    if m:
        journal = _strip_html(m.group(1)).strip()

    m = re.search(r"<h3>Abstract</h3>\s*<p>(.*?)</p>", summary, re.S)
    if m:
        abstract = _strip_html(m.group(1)).strip()
    else:
        abstract = _strip_html(summary)

    return authors, journal, abstract


def _extract_doi(entry) -> str:
    """Return a DOI URL from *entry* if present."""

    def _get(name):
        if isinstance(entry, dict):
            return entry.get(name)
        return getattr(entry, name, None)

    doi = _get("dc_identifier") or _get("doi") or ""
    if isinstance(doi, str) and doi.lower().startswith("doi:"):
        doi = doi.split("doi:", 1)[1].strip()
    if not doi:
        # Look for a DOI pattern in id or link fields
        for field in (_get("id"), _get("link")):
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

    summary_html = entry.get("summary") or entry.get("description") or ""
    abstract = _strip_html(summary_html)
    journal = entry.get("dc_source") or entry.get("source") or ""

    if (not authors or not journal) and "<strong>Authors:" in summary_html:
        a2, j2, abstract = _parse_longevity_summary(summary_html)
        if not authors:
            authors = a2
        if not journal:
            journal = j2

    return {
        "doi": _extract_doi(entry),
        "title": entry.get("title", ""),
        "authors": authors,
        "journal": journal,
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


def _doi_filename(doi: str) -> str:
    """Return a safe filename derived from *doi*.

    The resulting string is lowercase, keeps periods, converts forward slashes
    to underscores, and always starts with ``doiorg`` regardless of the DOI's
    original scheme.
    """
    if not doi:
        return ""
    doi = doi.lower().strip()
    doi = re.sub(r"^https?://(dx\.)?doi\.org/", "", doi)
    doi = re.sub(r"^doi:", "", doi)
    doi = doi.replace("/", "_")
    return "doiorg" + re.sub(r"[^a-z0-9._-]", "", doi)


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
            # Remove citation reference meta tags which may contain unrelated DOIs
            data = re.sub(
                r"<meta\s+[^>]*name=['\"]citation_reference['\"][^>]*>",
                "",
                data,
                flags=re.I | re.S,
            )
            print(f"Incoming opened URL")
            print(f"Data: {data}")
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


def _llm_primary_link(html: str) -> str:
    """Return the doi.org link that is the primary focus of this Fight Aging! HTML."""
    links = re.findall(r"href=['\"](https?://doi.org/[^'\"]+)['\"]", html, re.I)
    if not links:
        return ""
    if len(links) == 1:
        return links[0]

    snippet = html[:4000]
    sample = "\n".join(f"- {l}" for l in links[:20])
    client = openai.OpenAI()
    messages = [
        {
            "role": "system",
            "content": (
                "Choose the doi.org URL from the candidate list that links to the primary research paper discussed in the Fight Aging! HTML provided. Respond only with that URL."
            ),
        },
        {
            "role": "user",
            "content": f"HTML:\n```\n{snippet}\n```\n\nLinks:\n{sample}",
        },
    ]

    try:
        resp = client.chat.completions.create(
            model=THINKING_MODEL,
            messages=messages,
            max_completion_tokens=30,
        )
        text = resp.choices[0].message.content
    except Exception as exc:
        print(f"LLM link selection failed: {exc}")
        return ""

    m = re.search(r"https?://doi.org/[^\s]+", text)
    return m.group(0).strip() if m else links[0]


def _resolve_fightaging_item(url: str) -> tuple[str, str, str]:
    """Return the actual article link, DOI, and journal from a Fight Aging! post."""
    try:
        with urllib.request.urlopen(url) as resp:
            html = resp.read().decode("utf-8", errors="ignore")
    except Exception as exc:
        print(f"Failed to fetch {url}: {exc}")
        return url, "", ""

    doi = _llm_primary_link(html)
    if not doi:
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
    """Try to download a PDF for *entry* into *dest_dir*."""
    dest_dir.mkdir(exist_ok=True)
    before = set(dest_dir.glob("*.pdf"))

    def _getattr(obj, name):
        return obj.get(name, "") if isinstance(obj, dict) else getattr(obj, name, "")

    journal = (
        _getattr(entry, "journal")
        or _getattr(entry, "dc_source")
        or _getattr(entry, "source")
    ).strip()

    print(f"Journal appears to be: {journal}")
    used_custom = False
    lower_journal = journal.lower()
    if lower_journal == "nature communications":

        print(f"Nature Communications routine.")
        doi = _extract_doi(entry)
        if not doi:
            print(f"Confirming link: {getattr(entry, 'link', '')}")
            doi = _extract_doi_from_url(getattr(entry, 'link', ''))
        print(f"Doi appears to be: {doi}")
        if doi:
            script = _BASE_DIR / "pdf_fetch_natcomms.sh"
            cmd = [str(script), doi]
            print(f"Running Nature Communications script: {' '.join(cmd)}")
            try:
                subprocess.run(cmd, cwd=dest_dir, check=True)
                used_custom = True
            except Exception as exc:
                print(f"Nature Communications script failed: {exc}")

    if lower_journal == "nature aging":

        print(f"Nature Aging routine.")
        doi = _extract_doi(entry)
        if not doi:
            print(f"Confirming link: {getattr(entry, "link", "")}")
            doi = _extract_doi_from_url(getattr(entry, "link", ""))
        print(f"Doi appears to be: {doi}")
        if doi:
            script = _BASE_DIR / "pdf_fetch_nataging.sh"
            cmd = [str(script), doi]
            print(f"Running Nature Aging script: {' '.join(cmd)}")
            try:
                subprocess.run(cmd, cwd=dest_dir, check=True)
                used_custom = True
            except Exception as exc:
                print(f"Nature Aging script failed: {exc}")

    if lower_journal == "aging":

        print(f"Aging routine.")
        doi = _extract_doi(entry)
        if not doi:
            doi = _extract_doi_from_url(getattr(entry, "link", ""))
        print(f"Doi appears to be: {doi}")
        if doi:
            script = _BASE_DIR / "pdf_fetch_aging.sh"
            cmd = [str(script), doi]
            print(f"Running Aging script: {' '.join(cmd)}")
            try:
                subprocess.run(cmd, cwd=dest_dir, check=True)
                used_custom = True
            except Exception as exc:
                print(f"Aging script failed: {exc}")

    if lower_journal == "aging cell":

        print(f"Aging Cell routine.")
        doi = _extract_doi(entry)
        if not doi:
            doi = _extract_doi_from_url(getattr(entry, "link", ""))
        print(f"Doi appears to be: {doi}")
        if doi:
            #suffix = doi.split("/")[-1].replace(".", "_")
            suffix = doi.split("/")[-1]
            script = _BASE_DIR / "pdf_fetch_agingcell.sh"
            cmd = [str(script), suffix]
            print(f"Running Aging Cell script: {' '.join(cmd)}")
            try:
                subprocess.run(cmd, cwd=dest_dir, check=True)
                used_custom = True
            except Exception as exc:
                print(f"Aging Cell script failed: {exc}")

    if lower_journal == "geroscience":

        print(f"GeroScience routine.")
        doi = _extract_doi(entry)
        if not doi:
            doi = _extract_doi_from_url(getattr(entry, "link", ""))
        print(f"Doi appears to be: {doi}")
        if doi:
            script = _BASE_DIR / "pdf_fetch_geroscience.sh"
            cmd = [str(script), doi]
            print(f"Running GeroScience script: {' '.join(cmd)}")
            try:
                subprocess.run(cmd, cwd=dest_dir, check=True)
                used_custom = True
            except Exception as exc:
                print(f"GeroScience script failed: {exc}")

    if not used_custom:
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

    # Move the final PDF to the canonical storage directory
    final_dir = (_BASE_DIR / "../pdfs").resolve()
    final_dir.mkdir(parents=True, exist_ok=True)
    final_path = final_dir / chosen.name
    try:
        shutil.move(str(chosen), final_path)
    except Exception:
        # Fallback if moving fails for some reason
        chosen.replace(final_path)

    doi = getattr(entry, "doi", None) or _extract_doi(entry)
    if not doi:
        doi = _extract_doi_from_pdf(final_path)
    fname = _doi_filename(doi)
    if fname:
        target = final_dir / f"{fname}{final_path.suffix}"
        if target != final_path:
            try:
                final_path.rename(target)
                final_path = target
            except Exception as exc:
                print(f"DOI rename failed: {exc}")

    print(f"Downloaded PDF {final_path}")
    return final_path


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
    for feed_url, rss_title in _extract_feed_urls(opml_source, with_titles=True):
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
            article["rsstitle"] = rss_title
            articles[key] = article
            if download_pdfs:
                pdf_path = _download_pdf(entry, _PDF_DIR)
                articles[key]["download_successful"] = pdf_path is not None
                if pdf_path:
                    rel = pdf_path.relative_to(_PDF_DIR)
                    articles[key]["pdf"] = str(rel)
                    doi = _discover_doi(entry, pdf_path)
                    if doi:
                        articles[key]["doi"] = doi
                print(f"An update was made. {str(rel)}, {doi}")
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
        data["download_successful"] = pdf_path is not None
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


def download_journal_pdfs(
    journal: str,
    json_path: Path = _ARTICLES_JSON,
    max_articles: int | None = 1,
) -> None:
    """Download PDFs for articles in *json_path* matching *journal*.

    The ``journal`` comparison is case-insensitive. If *max_articles* is
    provided, stop after that many PDFs have been downloaded.
    """
    if not json_path.is_file():
        print(f"JSON file not found: {json_path}")
        return

    with json_path.open("r", encoding="utf-8") as fh:
        articles = json.load(fh)

    target = journal.strip().lower()
    updated = False
    processed = 0
    for key, data in articles.items():
        j = data.get("journal", "").strip().lower()
        if (
            j != target
            or data.get("pdf")
            or data.get("download_successful") is True
        ):
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
        entry.journal = data.get("journal", "")
        if "doi" in data and data["doi"]:
            entry.doi = data["doi"]

        print(f"Journal: {entry.journal}")
        print(f"Title: {entry.title}")
        print(f"Link: {entry.link}")
        print(f"DOI: {getattr(entry, 'doi', '')}")

        pdf_path = _download_pdf(entry, _PDF_DIR)
        print(f"PDF Path is {pdf_path}")
        data["download_successful"] = pdf_path is not None
        doi = None
        if pdf_path:
            rel = pdf_path.relative_to(_PDF_DIR)
            data["pdf"] = str(rel)
            doi = _discover_doi(entry, pdf_path)
            if doi:
                data["doi"] = doi
        updated = True
        processed += 1
        print(f"How far did we get? {doi}")
        time.sleep(random.uniform(5, 10))

    if updated:
        _save_articles(articles, json_path)


def pending_journal_articles(
    journal: str,
    json_path: Path = _ARTICLES_JSON,
) -> bool:
    """Return ``True`` if *json_path* contains an undownloaded article.

    The ``journal`` comparison ignores case. An article counts as pending if it
    matches the journal name and lacks a stored PDF or a successful download
    flag.
    """
    if not json_path.is_file():
        return False

    try:
        with json_path.open("r", encoding="utf-8") as fh:
            articles = json.load(fh)
    except Exception:
        return False

    target = journal.strip().lower()
    for data in articles.values():
        j = data.get("journal", "").strip().lower()
        if j != target:
            continue
        if data.get("pdf") or data.get("download_successful") is True:
            continue
        return True

    return False


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


def ocr_pdf(pdf_name: str, pdf_dir: Path = _PDF_DIR) -> Path | None:
    """Perform OCR on *pdf_name* and write ``.txt`` output.

    Image files generated during OCR are compressed into a ``.zip`` archive
    saved alongside the PDF and text files.

    Added troubleshooting messages for easier debugging of failures."""

    print(f"[OCR] Starting OCR for '{pdf_name}' in directory '{pdf_dir}'.")

    pdf_path = pdf_dir / pdf_name
    print(f"[OCR] Constructed PDF path: {pdf_path}")
    if not pdf_path.is_file():
        print(f"[OCR] PDF not found: {pdf_path}")
        return None

    if not shutil.which("tesseract"):
        print(
            "[OCR] Tesseract executable not found. Please install the 'tesseract-ocr' package."
        )
        return None

    txt_path = pdf_path.with_suffix(".txt")
    tmpdir = tempfile.mkdtemp(prefix="ocr_")
    print(f"[OCR] Temporary directory for image pages: {tmpdir}")
    try:
        print(f"[OCR] Running pdftoppm to convert PDF pages to images...")
        subprocess.run(
            [
                "pdftoppm",
                str(pdf_path),
                str(Path(tmpdir) / "page"),
                "-png",
            ],
            check=True,
        )

        images = sorted(Path(tmpdir).glob("page-*.png"))
        print(f"[OCR] Found {len(images)} page image(s) to process.")
        text_chunks: list[str] = []
        for img in images:
            print(f"[OCR] Processing image: {img}")
            result = subprocess.run(
                ["tesseract", str(img), "stdout", "-l", "eng"],
                capture_output=True,
                text=True,
                check=True,
            )
            text_chunks.append(result.stdout)
            print(f"[OCR] Wrote {len(result.stdout)} characters from {img}.")

        raw_text = "".join(text_chunks)

        client = openai.OpenAI()
        prompt = (
            "Below, I am pasting a scientific article that has been processed by OCR. "
            "I want you to clean up all the mistakes and reformat the text for readability. "
            "Do NOT summarize the text - your goal is strictly to correct OCR errors, not to alter the original article. "
            "You must preserve the original grammar, syntax, and spelling of the article."
        )
        messages = [
            {"role": "system", "content": prompt},
            {"role": "user", "content": raw_text},
        ]

        try:
            resp = client.chat.completions.create(
                model=SPEAKING_MODEL,
                messages=messages,
                max_completion_tokens=14000,
            )
            cleaned_text = resp.choices[0].message.content
        except Exception as exc:
            print(f"[OCR] LLM cleanup failed: {exc}")
            cleaned_text = raw_text

        with txt_path.open("w", encoding="utf-8") as out:
            out.write(cleaned_text)

        archive_path = pdf_path.with_suffix(".zip")
        print(f"[OCR] Saving page images to archive {archive_path}")
        try:
            with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                for img in images:
                    zf.write(img, arcname=img.name)
        except Exception as exc:
            print(f"[OCR] Failed to create archive: {exc}")
    except subprocess.CalledProcessError as exc:
        print(f"[OCR] Subprocess failed: {exc}")
        return None
    except Exception as exc:
        print(f"[OCR] Unexpected failure: {exc}")
        return None
    finally:
        print(f"[OCR] Cleaning up temporary directory {tmpdir}")
        shutil.rmtree(tmpdir, ignore_errors=True)

    print(f"[OCR] Saved OCR text to {txt_path}")
    return txt_path


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
