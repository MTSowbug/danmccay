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
from collections import Counter
import json

import os
import re
import urllib.parse
import urllib.request

import openai
from models import SPEAKING_MODEL, THINKING_MODEL, FETCH_MODEL
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


def _debug(message: str) -> None:
    """Emit a timestamped debug message for troubleshooting."""

    timestamp = _dt.datetime.now().isoformat(timespec="seconds")
    print(f"[feedfetchtest {timestamp}] {message}")

# Ensure a default articles store exists for convenience
_PDF_DIR.mkdir(parents=True, exist_ok=True)
if not _ARTICLES_JSON.is_file():
    _ARTICLES_JSON.write_text("{}", encoding="utf-8")


def _build_http_opener():
    return urllib.request.build_opener(
        urllib.request.HTTPRedirectHandler(),
    )


_HTTP_OPENER = _build_http_opener()
urllib.request.install_opener(_HTTP_OPENER)

_HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64; rv:126.0) "
        "Gecko/20100101 Firefox/126.0"
    ),
    "Accept": "*/*",
}


def _json_safe_copy(value):
    """Return *value* converted to JSON-serializable Python primitives."""

    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (_dt.datetime, _dt.date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_safe_copy(val) for key, val in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe_copy(item) for item in value]
    if hasattr(value, "__dict__"):
        return _json_safe_copy(vars(value))
    return str(value)


def _save_articles(articles: Dict[str, dict], output_path: Path) -> None:
    """Write *articles* to *output_path*, merging with any existing data."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    existing: Dict[str, dict] = {}
    existing_count = 0
    if output_path.is_file():
        with output_path.open("r", encoding="utf-8") as fh:
            try:
                existing = json.load(fh)
                existing_count = len(existing)
            except Exception as exc:
                _debug(
                    f"Failed to load existing JSON from {output_path}: {exc}. "
                    "Proceeding with an empty store."
                )
                existing = {}
                existing_count = 0

    safe_articles = _json_safe_copy(articles)
    if isinstance(articles, dict):
        # Remove keys that disappeared during sanitization
        for key in list(articles.keys()):
            if key not in safe_articles:
                del articles[key]

        # Update existing entries in-place so external references remain valid
        for key, safe_val in safe_articles.items():
            if isinstance(articles.get(key), dict) and isinstance(safe_val, dict):
                original = articles[key]
                original.clear()
                original.update(safe_val)
            else:
                articles[key] = safe_val

    if not isinstance(safe_articles, dict):
        _debug(
            "Incoming article payload for {path} is not a mapping; "
            "coercing to an empty dictionary.".format(path=output_path)
        )
        safe_articles = {}

    new_keys = [key for key in safe_articles if key not in existing]
    updated_keys = [key for key in safe_articles if key in existing]
    existing.update(safe_articles)
    safe_existing = _json_safe_copy(existing)
    _debug(
        "Merging articles into {path}. Incoming: {incoming} (new: {new}, updated: {updated}). "
        "Existing entries before merge: {before}. Total after merge: {after}.".format(
            path=output_path,
            incoming=len(safe_articles),
            new=len(new_keys),
            updated=len(updated_keys),
            before=existing_count,
            after=len(safe_existing),
        )
    )
    with output_path.open("w", encoding="utf-8") as fh:
        try:
            json.dump(safe_existing, fh, indent=2, sort_keys=True)
        except TypeError as exc:
            _debug(
                "Primary serialization for {path} failed due to {exc!s}; "
                "retrying with best-effort string coercion.".format(
                    path=output_path, exc=exc
                )
            )
            fh.seek(0)
            fh.truncate()
            json.dump(safe_existing, fh, indent=2, sort_keys=True, default=str)
    _debug(
        f"Finished writing {len(safe_existing)} articles to {output_path}. "
        f"File size is now {output_path.stat().st_size} bytes."
    )


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
    _debug(
        "Extracted {count} feeds from {source}".format(
            count=len(feeds), source=opml_source
        )
    )
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
        "mt-relevance": 0,
        "st-relevance": 0,
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


def _html_links_only(html: str) -> str:
    """Return *html* reduced to a list of cleaned ``<a>`` tags."""

    timestamp = int(time.time() * 1000)
    before_path = Path(f"/tmp/html_before_{timestamp}.html")
    try:
        before_path.write_text(html, encoding="utf-8")
    except Exception as exc:
        print(f"Could not write {before_path}: {exc}")

    # Keep only anchor tags
    html = re.sub(
        r"(?is)(<a\b[^>]*>.*?</a>)|<[^>]+|[^<]+",
        lambda m: m.group(1) or "",
        html,
    )

    # Normalize href attribute
    html = re.sub(
        r"(?is)<a\b[^>]*?\bhref\s*=\s*(['\"]?)([^\s'\">]+)\1[^>]*>",
        r'<a href="\2">',
        html,
    )

    # Strip tags other than <a>
    html = re.sub(r"(?is)<(?!/?a\b)[^>]+>", "", html)

    # Drop non-http links
    #html = re.sub(
    #    r"(?is)<a\b[^>]*\bhref\s*=\s*(['\"]?)(?!https?:\/\/)[^'\">\s]+\1[^>]*>.*?</a>\s*",
    #    "",
    #    html,
    #)

    # Put each link on its own line
    html = re.sub(r"</a>", "</a>\n", html)

    after_path = Path(f"/tmp/html_after_{timestamp}.html")
    try:
        after_path.write_text(html, encoding="utf-8")
    except Exception as exc:
        print(f"Could not write {after_path}: {exc}")

    return html.strip()


_DOI_HOSTS = {"doi.org", "www.doi.org", "dx.doi.org"}


def _determine_effective_url(
    requested_url: str,
    reported_url: str,
    html: str,
) -> str:
    """Return the most plausible canonical URL for *html*.

    Browsing often begins from a doi.org link which subsequently redirects to the
    publisher's site.  When the fetch script fails to report the final URL, we
    must infer it from the HTML so that any relative links are joined against
    the correct domain.
    """

    fallback = reported_url or requested_url or ""
    base_source = fallback or requested_url or ""

    if not html:
        return fallback

    patterns = [
        r"<base[^>]+href\s*=\s*['\"]([^'\"]+)['\"]",
        r"<link[^>]+rel\s*=\s*['\"]canonical['\"][^>]*href\s*=\s*['\"]([^'\"]+)['\"]",
        r"<meta[^>]+property\s*=\s*['\"]og:url['\"][^>]*content\s*=\s*['\"]([^'\"]+)['\"]",
        r"<meta[^>]+name\s*=\s*['\"]og:url['\"][^>]*content\s*=\s*['\"]([^'\"]+)['\"]",
        r"<meta[^>]+property\s*=\s*['\"]citation_public_url['\"][^>]*content\s*=\s*['\"]([^'\"]+)['\"]",
        r"<meta[^>]+name\s*=\s*['\"]citation_public_url['\"][^>]*content\s*=\s*['\"]([^'\"]+)['\"]",
        r"<meta[^>]+property\s*=\s*['\"]citation_fulltext_html_url['\"][^>]*content\s*=\s*['\"]([^'\"]+)['\"]",
        r"<meta[^>]+name\s*=\s*['\"]citation_fulltext_html_url['\"][^>]*content\s*=\s*['\"]([^'\"]+)['\"]",
        r"<meta[^>]+http-equiv\s*=\s*['\"]refresh['\"][^>]*content\s*=\s*['\"][^'\"]*url=([^'\" >;]+)",
    ]

    for pattern in patterns:
        match = re.search(pattern, html, flags=re.I)
        if not match:
            continue
        candidate = match.group(1).strip()
        if not candidate:
            continue
        resolved = urllib.parse.urljoin(base_source, candidate)
        parsed = urllib.parse.urlparse(resolved)
        if parsed.scheme in {"http", "https"} and parsed.netloc:
            return resolved

    parsed_source = urllib.parse.urlparse(base_source)
    fallback_host = parsed_source.netloc.lower()

    if fallback_host and fallback_host not in _DOI_HOSTS:
        return fallback

    host_counts: Counter[str] = Counter()
    for link in re.findall(r"<a[^>]+href=\s*['\"](https?://[^'\"]+)['\"]", html, flags=re.I):
        parsed_link = urllib.parse.urlparse(link)
        host = parsed_link.netloc.lower()
        if host:
            host_counts[host] += 1

    for host, count in host_counts.most_common():
        if host and host not in _DOI_HOSTS and count >= 2:
            scheme = (
                parsed_source.scheme
                or urllib.parse.urlparse(requested_url or "").scheme
                or "https"
            )
            return urllib.parse.urlunparse((scheme, host, "/", "", "", ""))

    return fallback


def _llm_shell_commands(entry, dest_dir: Path) -> str:
    """Use an LLM-guided browsing loop to download *entry* as a PDF."""
    url = getattr(entry, "link", "") or getattr(entry, "doi", "")
    if not url:
        print("No link available for entry")
        return ""

    client = openai.OpenAI()
    #script_path = (_BASE_DIR / "pdf_fetch_generic.sh").resolve()
    script_path = (_BASE_DIR / "pdf_fetch_generic_curl.sh").resolve()
    if not script_path.is_file():
        print(f"Fetch script not found at {script_path}")
        return ""

    def _fetch(u: str) -> tuple[bytes, str, str]:
        temp_file = dest_dir / "tempfile"
        temp_file.unlink(missing_ok=True)

        env = os.environ.copy()

        try:
            result = subprocess.run(
                ["bash", str(script_path), u],
                cwd=str(dest_dir),
                capture_output=True,
                text=True,
                env=env,
            )
        except Exception as exc:
            raise RuntimeError(f"Failed to launch fetch script: {exc}") from exc

        if result.returncode != 0:
            output = (result.stderr or "").strip() or (result.stdout or "").strip()
            raise RuntimeError(
                f"Fetch script exited with {result.returncode} for {u}: {output}"
            )

        if not temp_file.exists():
            raise RuntimeError("Fetch script did not produce an output file")

        data = temp_file.read_bytes()
        temp_file.unlink(missing_ok=True)

        transcript = "\n".join(filter(None, [result.stdout, result.stderr]))
        urls = re.findall(r"https?://\S+", transcript)
        final_url = urls[-1].rstrip('\"\'') if urls else u

        if data.startswith(b"%PDF"):
            ctype = "application/pdf"
        else:
            ctype = "text/html"

        return data, ctype, final_url

    visited: list[str] = []
    for i in range(5):
        if url in visited:
            print("Encountered a repeated URL; aborting")
            break
        print(f"Attempt {i + 1}: fetching {url}")
        try:
            data, ctype, final_url = _fetch(url)
        except Exception as exc:
            print(f"Failed to fetch {url}: {exc}")
            break

        fallback_url = final_url or url
        for candidate in (url, fallback_url):
            if candidate and candidate not in visited:
                visited.append(candidate)

        if ctype.startswith("application/pdf") or data.startswith(b"%PDF"):
            pdf_path = dest_dir / f"article_fulltext_version{i + 1}.pdf"
            try:
                pdf_path.write_bytes(data)
                print(f"Saved PDF {pdf_path}")
            except Exception as exc:
                print(f"Failed to save PDF: {exc}")
            return f"Downloaded {fallback_url}"

        html = data.decode("utf-8", errors="ignore")

        page_url = _determine_effective_url(url, final_url, html)
        base_url = page_url or fallback_url
        if base_url and base_url not in visited:
            visited.append(base_url)

        snippet = _html_links_only(html)
        print(f"Cleaned HTML: {snippet}")

        info_parts = []
        title = getattr(entry, "title", "")
        if title:
            info_parts.append(f"Title: {title}")
        journal = getattr(entry, "journal", "")
        if journal:
            info_parts.append(f"Journal: {journal}")
        doi = getattr(entry, "doi", "")
        if doi:
            info_parts.append(f"DOI: {doi}")
        authors = getattr(entry, "authors", [])
        if authors:
            info_parts.append(f"Authors: {', '.join(authors)}")
        year = getattr(entry, "year", None)
        if year:
            info_parts.append(f"Year: {year}")
        context = "\n".join(info_parts)

        messages = [
            {
                "role": "system",
                "content": (
                    f"Identify the link in this HTML that most likely leads to the full-text PDF of the corresponding scientific article. "
                    f"You are currently viewing {base_url or fallback_url}. Do not pick a link that loads this same page again. "
                    "Respond only with that URL. Your URL must appear VERBATIM within the HTML listed below. "
                    "DO NOT MODIFY THESE LINKS. Your URL does not have to lead directly to the PDF, but it must lead "
                    "the user closer to the PDF. Some PDF links are misleading - try to avoid links to supplementary "
                    "information, citations, or unrelated papers."
                ),
            },
        ]
        if context:
            messages.append({"role": "system", "content": f"Article information:\n{context}"})

        current_view = base_url or fallback_url
        prior_links = [v for v in visited if v and v != current_view]
        if prior_links:
            msg = (
                "These links have already been visited and NONE of them should be"
                " revisited:\n" + "\n".join(prior_links)
            )
            messages.append({"role": "system", "content": msg})

        messages.append({"role": "user", "content": snippet})
        try:
            resp = client.chat.completions.create(
                model=FETCH_MODEL,
                messages=messages,
                max_completion_tokens=2000,
                reasoning_effort="low"
            )
            guess = resp.choices[0].message.content.strip()
        except Exception as exc:
            print(f"LLM request failed: {exc}")
            break

        m = re.search(r'https?://\S+', guess)
        if m:
            url_candidate = m.group(0)
        else:
            pieces = guess.strip().split()
            if not pieces:
                print(f"LLM response did not contain a URL: {guess}")
                break
            url_candidate = pieces[0].strip("'\"()<>,.")
        base_for_join = current_view
        if base_for_join:
            parsed_base = urllib.parse.urlparse(base_for_join)
        else:
            parsed_base = None
        if (
            base_for_join
            and parsed_base
            and parsed_base.path
            and not parsed_base.path.endswith("/")
            and not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", url_candidate)
            and not url_candidate.startswith(("/", "#"))
        ):
            base_for_join = urllib.parse.urlunparse(
                parsed_base._replace(path=f"{parsed_base.path}/")
            )
        url = urllib.parse.urljoin(base_for_join or fallback_url, url_candidate)

    return ""


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
            #print(f"Data: {data}")
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

    if lower_journal == 'translational cancer research':
        
        print(f"Translational Cancer Research routine.")
        doi = _extract_doi(entry)
        if not doi:
            doi = _extract_doi_from_url(getattr(entry, "link", ""))
        print(f"Doi appears to be: {doi}")
        if doi:
            script = _BASE_DIR / "pdf_fetch_tcr.sh"
            cmd = [str(script), doi]
            print(f"Running Translational Cancer Research script: {' '.join(cmd)}")
            try:
                subprocess.run(cmd, cwd=dest_dir, check=True)
                used_custom = True
            except Exception as exc:
                print(f"Translational Cancer Research script failed: {exc}")        

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


def _cleanup_ocr_text(raw_text: str) -> str:
    """Use the LLM cleanup step shared by OCR flows."""

    if not raw_text:
        return ""

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
        return resp.choices[0].message.content
    except Exception as exc:
        print(f"[OCR] LLM cleanup failed: {exc}")
        return raw_text


def _ocr_pdf_fallback(pdf_path: Path, txt_path: Path) -> Path | None:
    """Extract text directly from *pdf_path* when OCR binaries are unavailable."""

    try:
        from PyPDF2 import PdfReader

        reader = PdfReader(str(pdf_path))
        raw_text = ""
        for page in reader.pages:
            try:
                raw_text += page.extract_text() or ""
            except Exception as exc:
                print(f"[OCR] Failed to read page text during fallback: {exc}")
                continue
    except Exception as exc:
        print(f"[OCR] Fallback text extraction failed: {exc}")
        return None

    cleaned_text = _cleanup_ocr_text(raw_text)
    try:
        txt_path.write_text(cleaned_text, encoding="utf-8")
    except Exception as exc:
        print(f"[OCR] Failed to write fallback text: {exc}")
        return None

    archive_path = pdf_path.with_suffix(".zip")
    payload = cleaned_text.encode("utf-8") or b"Fallback OCR placeholder"
    try:
        with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("page-1.txt", payload)
    except Exception as exc:
        print(f"[OCR] Failed to create fallback archive: {exc}")

    print(f"[OCR] Saved fallback OCR text to {txt_path}")
    return txt_path


def fetch_pdf_for_article(title: str, dest_dir: Path = _PDF_DIR) -> Path | None:
    """Try to fetch a PDF for *title* using CrossRef search."""

    query = urllib.parse.quote(title)
    url = f"https://api.crossref.org/works?query.title={query}&rows=1"
    try:
        with urllib.request.urlopen(url) as resp:
            data = json.load(resp)
    except Exception as exc:
        print(f"CrossRef lookup failed: {exc}")
        return None

    items = data.get("message", {}).get("items", [])
    if not items:
        print(f"No CrossRef result for {title}")
        return None

    item = items[0]
    doi = item.get("DOI", "")
    journal = ""
    if item.get("container-title"):
        journal = item["container-title"][0]
    link = f"https://doi.org/{doi}" if doi else ""

    class Entry:
        pass

    entry = Entry()
    entry.title = title
    entry.link = link
    entry.journal = journal
    if doi:
        entry.doi = link

    return _download_pdf(entry, dest_dir)


def fetch_pdf_for_doi(doi: str, dest_dir: Path = _PDF_DIR) -> Path | None:
    """Try to fetch a PDF for *doi* using CrossRef metadata."""

    doi = doi.strip()
    lower = doi.lower()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi.org/", "doi:"):
        if lower.startswith(prefix):
            doi = doi[len(prefix):].strip()
            lower = doi.lower()
            break

    api_url = f"https://api.crossref.org/works/{urllib.parse.quote(doi)}"
    title = ""
    journal = ""
    try:
        with urllib.request.urlopen(api_url) as resp:
            data = json.load(resp)
            msg = data.get("message", {})
            if msg.get("title"):
                title = msg["title"][0]
            if msg.get("container-title"):
                journal = msg["container-title"][0]
    except Exception as exc:
        print(f"CrossRef lookup failed: {exc}")

    link = f"https://doi.org/{doi}"

    # Load the article store and either locate or create a matching item
    articles: Dict[str, dict] = {}
    try:
        with _ARTICLES_JSON.open("r", encoding="utf-8") as fh:
            articles = json.load(fh)
    except Exception:
        articles = {}

    article_key = None
    article = None
    for key, data in articles.items():
        if data.get("doi") == link:
            article_key = key
            article = data
            break

    if article is None:
        article_key = link
        article = {
            "title": title or doi,
            "link": link,
            "journal": journal,
            "doi": link,
        }
        articles[article_key] = article

    class Entry:
        pass

    entry = Entry()
    entry.title = article.get("title") or title or doi
    entry.link = link
    entry.journal = article.get("journal", journal)
    entry.doi = link

    pdf_path = _download_pdf(entry, dest_dir)
    article["download_successful"] = pdf_path is not None
    if pdf_path:
        rel = pdf_path.relative_to(dest_dir)
        article["pdf"] = str(rel)
        doi_found = _discover_doi(entry, pdf_path)
        if doi_found:
            article["doi"] = doi_found
    _save_articles(articles, _ARTICLES_JSON)

    txt_path = None
    if pdf_path is not None:
        try:
            txt_path = ocr_pdf(pdf_path.name, dest_dir)
        except Exception as exc:
            print(f"OCR failed: {exc}")

    if txt_path is not None:
        abstract = ""
        try:
            raw_text = txt_path.read_text(encoding="utf-8")
        except Exception as exc:
            print(f"Failed to read OCR text: {exc}")
            raw_text = ""

        if raw_text:
            client = openai.OpenAI()
            messages = [
                {
                    "role": "system",
                    "content": (
                        "Identify and return only the complete Abstract section from the following OCR extracted text."
                    ),
                },
                {"role": "user", "content": raw_text[:20000]},
            ]

            try:
                resp = client.chat.completions.create(
                    model=SPEAKING_MODEL,
                    messages=messages,
                    max_completion_tokens=500,
                )
                abstract = resp.choices[0].message.content.strip()
            except Exception as exc:
                print(f"LLM abstract extraction failed: {exc}")

        if abstract:
            article["abstract"] = abstract
            _save_articles(articles, _ARTICLES_JSON)
            try:
                analyze_article(abstract, pdf_path)
            except Exception as exc:
                print(f"Analysis failed: {exc}")

    return pdf_path


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

    _debug(
        "Starting fetch_recent_articles with opml_source={source}, hours={hours}, "
        "download_pdfs={download}, json_path={json}. Cutoff timestamp: {cutoff}.".format(
            source=opml_source,
            hours=hours,
            download=download_pdfs,
            json=json_path,
            cutoff=cutoff.isoformat(),
        )
    )

    for feed_url, rss_title in _extract_feed_urls(opml_source, with_titles=True):
        parsed = _fp.parse(feed_url)
        total_entries = len(getattr(parsed, "entries", []))
        _debug(
            "Processing feed '{title}' ({url}). Total entries: {total}.".format(
                title=rss_title or "<untitled>", url=feed_url, total=total_entries
            )
        )
        if getattr(parsed, "bozo", False):
            _debug(
                "Feedparser reported an issue with {url}: {error}".format(
                    url=feed_url,
                    error=getattr(parsed, "bozo_exception", "unknown error"),
                )
            )

        matched_entries = 0

        for entry in parsed.entries:
            ts = _entry_timestamp(entry)
            if ts is None or ts < cutoff:
                continue
            matched_entries += 1

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

        _debug(
            "Feed '{title}' contributed {matched} entries newer than cutoff.".format(
                title=rss_title or "<untitled>", matched=matched_entries
            )
        )

    if json_path is not None:
        _debug(
            f"Completed aggregation of {len(articles)} articles. Writing to {json_path}."
        )
        _save_articles(articles, Path(json_path))
    else:
        _debug(
            f"Completed aggregation of {len(articles)} articles. Skipping write step."
        )

    _debug(f"fetch_recent_articles returning {len(articles)} articles.")
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
        if data.get("download_successful") is False:
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
        entry.journal = (
            data.get("journal")
            or data.get("dc_source")
            or data.get("source")
            or ""
        )
        
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


def journals_with_pending_articles(
    json_path: Path = _ARTICLES_JSON,
) -> dict[str, str]:
    """Return a mapping of lower journal names to canonical names for pending articles."""
    if not json_path.is_file():
        return {}

    try:
        with json_path.open("r", encoding="utf-8") as fh:
            articles = json.load(fh)
    except Exception:
        return {}

    result: dict[str, str] = {}
    for data in articles.values():
        j = data.get("journal", "").strip()
        if not j:
            continue
        if data.get("pdf") or data.get("download_successful") is True:
            continue
        lower = j.lower()
        result.setdefault(lower, j)

    return result


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


def analyze_article(
    abstract: str,
    pdf_path: Path,
    char_file: Path | str = (_BASE_DIR / "danmccay.yaml"),
) -> str:
    """Return an LLM analysis of *abstract* and save it next to *pdf_path*.

    The saved text starts with the abstract followed by a blank line and then
    the analysis.  This allows downstream consumers to access the abstract
    directly from the ``.analysis.txt`` file.
    """

    try:
        with Path(char_file).open("r", encoding="utf-8") as fh:
            core = yaml.safe_load(fh)
        brain = core.get("prompts", {}).get("brain", {})
        preamble = brain.get("relevance_preamble", "")
        postamble = brain.get("relevance_postamble", "")
    except Exception as exc:
        print(f"Failed to load analysis prompts: {exc}")
        preamble = ""
        postamble = ""

    prompt = f"{preamble}\n\n{abstract.strip()}\n\n{postamble}".strip()

    client = openai.OpenAI()
    messages = [{"role": "system", "content": prompt}]

    try:
        resp = client.chat.completions.create(
            model=THINKING_MODEL,
            messages=messages,
            max_completion_tokens=10000,
        )
        analysis = resp.choices[0].message.content
    except Exception as exc:
        print(f"LLM analysis failed: {exc}")
        analysis = ""

    out_path = pdf_path.with_suffix(".analysis.txt")
    try:
        out_path.write_text(f"{abstract.strip()}\n\n{analysis}", encoding="utf-8")
    except Exception as exc:
        print(f"Failed to save analysis text: {exc}")

    # Parse relevance scores from the analysis
    scores = {}
    for i, field in [(1, "lt-relevance"), (2, "mt-relevance"), (3, "st-relevance")]:
        m = re.search(
            rf"\[\[SECTION\s*{i}\]\].*?<<[^:>]*:\s*(\d+)\s*>>",
            analysis,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if m:
            try:
                scores[field] = int(m.group(1))
            except Exception:
                pass

    if scores:
        try:
            with _ARTICLES_JSON.open("r", encoding="utf-8") as fh:
                articles = json.load(fh)
        except Exception:
            articles = {}

        rel = None
        try:
            rel = str(Path(pdf_path).resolve().relative_to(_PDF_DIR))
        except Exception:
            rel = pdf_path.name

        updated = False
        for data in articles.values():
            if data.get("pdf") == rel:
                data.update(scores)
                updated = True
                break

        if updated:
            _save_articles(articles, _ARTICLES_JSON)

    return analysis


def design_experiment_for_doi(
    doi: str,
    pdf_dir: Path = _PDF_DIR,
    char_file: Path | str = (_BASE_DIR / "danmccay.yaml"),
) -> str:
    """Return an LLM-designed experiment for the article identified by *doi*.

    The function reads the OCR text from ``pdf_dir`` and uses the designer
    prompts from *char_file* to generate a proposal. The output is saved next to
    the text file with a ``.exp.txt`` suffix."""

    fname = _doi_filename(doi)
    txt_path = pdf_dir / f"{fname}.txt"
    if not txt_path.is_file():
        print(f"Text not found: {txt_path}")
        return ""

    try:
        full_text = txt_path.read_text(encoding="utf-8")
    except Exception as exc:
        print(f"Failed to read text: {exc}")
        return ""

    try:
        with Path(char_file).open("r", encoding="utf-8") as fh:
            core = yaml.safe_load(fh)
        brain = core.get("prompts", {}).get("brain", {})
        pre = brain.get("designer_preamble", "")
        post = brain.get("designer_postamble", "")
    except Exception as exc:
        print(f"Failed to load designer prompts: {exc}")
        pre = ""
        post = ""

    prompt = f"{pre}\n\n{full_text.strip()}\n\n{post}".strip()
    client = openai.OpenAI()
    messages = [{"role": "system", "content": prompt}]

    try:
        resp = client.chat.completions.create(
            model=THINKING_MODEL,
            messages=messages,
            max_completion_tokens=20000,
        )
        design = resp.choices[0].message.content
    except Exception as exc:
        print(f"LLM design failed: {exc}")
        design = ""

    out_path = pdf_dir / f"{fname}.exp.txt"
    try:
        out_path.write_text(design, encoding="utf-8")
    except Exception as exc:
        print(f"Failed to save design text: {exc}")

    return design


def design_experiment_for_file(
    txt_file: str | Path,
    char_file: Path | str = (_BASE_DIR / "danmccay.yaml"),
) -> str:
    """Return an LLM-designed experiment for the OCR text in *txt_file*.

    The function mirrors :func:`design_experiment_for_doi` but operates on a
    direct text file path. The resulting design is saved next to the text file
    with a ``.exp.txt`` suffix."""

    txt_path = Path(txt_file)
    print(f"[DESIGN] Preparing to design experiment for {txt_path}")
    if not txt_path.is_file():
        print(f"[DESIGN] Text not found: {txt_path}")
        return ""

    try:
        full_text = txt_path.read_text(encoding="utf-8")
        print(f"[DESIGN] Read {len(full_text)} characters from {txt_path}")
    except Exception as exc:
        print(f"[DESIGN] Failed to read text: {exc}")
        return ""

    try:
        with Path(char_file).open("r", encoding="utf-8") as fh:
            core = yaml.safe_load(fh)
        brain = core.get("prompts", {}).get("brain", {})
        pre = brain.get("designer_preamble", "")
        post = brain.get("designer_postamble", "")
        print(f"[DESIGN] Loaded designer prompts from {char_file}")
    except Exception as exc:
        print(f"[DESIGN] Failed to load designer prompts: {exc}")
        pre = ""
        post = ""

    prompt = f"{pre}\n\n{full_text.strip()}\n\n{post}".strip()
    print(f"[DESIGN] Prompt length: {len(prompt)} characters")
    client = openai.OpenAI()
    messages = [{"role": "system", "content": prompt}]

    try:
        resp = client.chat.completions.create(
            model=THINKING_MODEL,
            messages=messages,
            max_completion_tokens=20000,
        )
        design = resp.choices[0].message.content
        print("[DESIGN] Received design from LLM")
    except Exception as exc:
        print(f"[DESIGN] LLM design failed: {exc}")
        design = ""

    out_path = txt_path.with_suffix(".exp.txt")
    try:
        out_path.write_text(design, encoding="utf-8")
        print(f"[DESIGN] Saved design to {out_path}")
    except Exception as exc:
        print(f"[DESIGN] Failed to save design text: {exc}")

    return design


def design_experiments_from_analyses(
    pdf_dir: Path = _PDF_DIR,
    char_file: Path | str = (_BASE_DIR / "danmccay.yaml"),
) -> list[Path]:
    """Design experiments for all analyses created today in *pdf_dir*.

    Returns a list of processed text file paths."""

    print(f"[BATCH] Designing experiments in {pdf_dir} using {char_file}")

    today = _dt.datetime.now().date()
    week_ago = today - _dt.timedelta(days=7)
    processed: list[Path] = []
    analyses = list(Path(pdf_dir).glob("*.analysis.txt"))
    print(f"[BATCH] Found {len(analyses)} analysis file(s)")

    for analysis in analyses:
        print(f"[BATCH] Examining {analysis}")
        try:
            mtime = _dt.datetime.fromtimestamp(analysis.stat().st_mtime).date()
            print(f"[BATCH] mtime for {analysis}: {mtime}")
        except Exception as exc:
            print(f"[BATCH] Failed to read mtime for {analysis}: {exc}")
            continue
        #if mtime != today:
        #    continue

        txt_path = analysis.with_name(analysis.name.replace(".analysis.txt", ".txt"))
        exp_path = txt_path.with_suffix(".exp.txt")
        if not txt_path.is_file():
            print(f"[BATCH] Text file missing for {analysis}")
            continue
        if exp_path.is_file():
            print(f"[BATCH] Experiment already exists for {txt_path}")
            continue

        design_experiment_for_file(txt_path, char_file=char_file)
        schematize_experiment(exp_path)
        processed.append(txt_path)
        print(f"[BATCH] Completed processing for {txt_path}")

    candidates: set[Path] = set(processed)
    for schema in Path(pdf_dir).glob("*.schema.txt"):
        try:
            mtime = _dt.datetime.fromtimestamp(schema.stat().st_mtime).date()
        except Exception:
            continue
        if mtime < week_ago:
            continue
        base = schema.with_name(schema.name.replace(".schema.txt", ".txt"))
        if base.is_file():
            candidates.add(base)

    if candidates:
        print(f"[BATCH] {len(processed)} file(s) processed; updating wellplate")
        try:
            with _ARTICLES_JSON.open("r", encoding="utf-8") as fh:
                articles = json.load(fh)
            print(f"[BATCH] Loaded article metadata from {_ARTICLES_JSON}")
        except Exception as exc:
            print(f"[BATCH] Failed to read articles JSON: {exc}")
            articles = {}

        scores = {}
        for data in articles.values():
            if not isinstance(data, dict):
                continue
            pdf = data.get("pdf")
            if not pdf:
                continue
            lt = data.get("lt-relevance", 0)
            mt = data.get("mt-relevance", 0)
            st = data.get("st-relevance", 0)
            scores[pdf] = lt + 2 * mt + 3 * st

        def _score(p: Path) -> int:
            pdf_rel = p.with_suffix(".pdf")
            try:
                pdf_rel = pdf_rel.resolve().relative_to(Path(pdf_dir).resolve())
            except Exception:
                pdf_rel = pdf_rel.name
            return scores.get(str(pdf_rel), 0)

        ordered = sorted(candidates, key=lambda p: (-_score(p), p.name))

        def _extract_rows(sql: str) -> list[str]:
            """Return a list of individual ``INSERT`` row strings."""

            stmts = re.findall(r"INSERT\s+INTO[^;]*;", sql, flags=re.I | re.S)
            rows: list[str] = []
            for stmt in stmts:
                stmt = stmt.strip().rstrip(";")
                if "values" in stmt.lower():
                    head, tail = re.split(r"(?i)values", stmt, maxsplit=1)
                    values = tail.strip()
                    groups = re.findall(r"\([^)]*\)", values)
                    if not groups:
                        groups = [values]
                    for g in groups:
                        rows.append(f"{head.strip()} VALUES {g}")
                else:
                    rows.append(stmt)
            return rows

        def _extract_alters(sql: str) -> list[str]:
            """Return a list of ``ALTER TABLE`` statements."""

            pattern = r"ALTER\s+(?:TABLE\s+)?trialsv2db[^;]*;"
            alters = re.findall(pattern, sql, flags=re.I | re.S)
            return [a.strip().rstrip(";") for a in alters]

        def _append_column_value(stmt: str, column: str, value: str) -> str:
            """Return *stmt* with ``column`` and ``value`` appended."""
            pattern = r"(?i)(insert\s+into\s+trialsv2db)(?:\s*\(([^)]*)\))?\s*values\s*\(([^)]*)\)"
            m = re.match(pattern, stmt.strip())
            if not m:
                return stmt
            prefix, cols, vals = m.groups()
            cols = ", ".join(filter(None, [cols.strip() if cols else "", column]))
            vals = ", ".join(filter(None, [vals.strip(), value]))
            return f"{prefix}({cols}) VALUES ({vals})"

        def _backtick_columns(sql: str) -> str:
            """Return *sql* with all column names wrapped in backticks."""

            def _bt(name: str) -> str:
                name = name.strip()
                if not name:
                    return name
                if name.startswith("`") and name.endswith("`"):
                    return name
                return f"`{name.strip('`')}" + "`"

            insert_pat = re.search(r"(?i)insert\s+into\s+trialsv2db\s*\(([^)]*)\)", sql)
            if insert_pat:
                cols = insert_pat.group(1)
                col_list = [c.strip() for c in cols.split(',') if c.strip()]
                cols_bt = ", ".join(_bt(c) for c in col_list)
                sql = sql[: insert_pat.start(1)] + cols_bt + sql[insert_pat.end(1):]

            alter_pat = re.search(r"(?i)(alter\s+(?:table\s+)?trialsv2db\s+add\s+column\s+)([^\s]+)", sql)
            if alter_pat:
                col = alter_pat.group(2)
                sql = sql[: alter_pat.start(2)] + _bt(col) + sql[alter_pat.end(2):]

            return sql

        wellplate = Path(pdf_dir) / f"{today.isoformat()}_wellplate.txt"
        print(f"[BATCH] Writing wellplate to {wellplate}")

        seen: set[str] = set()
        base_rows: list[str] = []
        alter_rows: list[str] = []
        total = 0

        for txt_path in ordered:
            schema_path = txt_path.with_suffix(".schema.txt")
            if not schema_path.is_file():
                continue
            try:
                schema_text = schema_path.read_text(encoding="utf-8")
            except Exception:
                continue

            rows = [_backtick_columns(r) for r in _extract_rows(schema_text) if r.strip()]
            alters = [_backtick_columns(r) for r in _extract_alters(schema_text) if r.strip()]
            unique_rows = []
            unique_alters = []
            file_seen: set[str] = set()
            for r in rows:
                norm = r.strip().lower()
                if norm not in seen and norm not in file_seen:
                    unique_rows.append(r)
                    file_seen.add(norm)
            for r in alters:
                norm = r.strip().lower()
                if norm not in seen and norm not in file_seen:
                    unique_alters.append(r)
                    file_seen.add(norm)
            if total + len(unique_rows) > 12:
                continue
            for r in unique_alters:
                seen.add(r.strip().lower())
                alter_rows.append(_backtick_columns(r.rstrip(";").strip()) + ";")

            for r in unique_rows:
                seen.add(r.strip().lower())
                base = _append_column_value(r.rstrip(";").strip(), "status", "'pending'")
                base_rows.append(_backtick_columns(base))
                total += 1
                if total >= 12:
                    break
            if total >= 12:
                break

        mapping = [
            ("A1", 1), ("A2", 5), ("A3", 8), ("A4", 12), ("A5", 1), ("A6", 11),
            ("B1", 2), ("B2", 6), ("B3", 9), ("B4", 3), ("B5", 10), ("B6", 7),
            ("C1", 3), ("C2", 7), ("C3", 10), ("C4", 5), ("C5", 4), ("C6", 12),
            ("D1", 4), ("D2", 8), ("D3", 11), ("D4", 6), ("D5", 2), ("D6", 9),
        ]

        final_rows = []
        final_rows.extend(alter_rows)
        for well, idx in mapping:
            if 0 < idx <= len(base_rows):
                row = _append_column_value(base_rows[idx - 1].rstrip(), "well", f"'{well}'")
                final_rows.append(_backtick_columns(row).rstrip() + ";")

        try:
            with wellplate.open("w", encoding="utf-8") as wh:
                wh.write("\n".join(final_rows) + ("\n" if final_rows else ""))
        except Exception as exc:
            print(f"Failed to create wellplate file: {exc}")

        print(f"[BATCH] Wrote {len(final_rows)} statement(s) to wellplate")

    return processed


def schematize_experiment(
    exp_file: str | Path,
    schema_file: Path = Path("schema.txt"),
) -> str:
    """Convert a prose experiment into a schema-based SQL row.

    The returned text is also saved next to *exp_file* with a ``.schema.txt``
    suffix."""

    exp_path = Path(exp_file)
    print(f"[SCHEMA] Schematizing experiment {exp_path}")
    try:
        exp_text = exp_path.read_text(encoding="utf-8")
        print(f"[SCHEMA] Read {len(exp_text)} characters from {exp_path}")
    except Exception as exc:
        print(f"[SCHEMA] Failed to read experiment text: {exc}")
        return ""

    # Only send the first paragraph of the experiment text to the LLM
    first_para = exp_text.strip().split("\n\n", 1)[0]

    # Load schematizer prompts from the character file
    try:
        with (_BASE_DIR / "danmccay.yaml").open("r", encoding="utf-8") as fh:
            core = yaml.safe_load(fh)
        brain = core.get("prompts", {}).get("brain", {})
        pre = brain.get("schematizer_preamble", "")
        post = brain.get("schematizer_postamble", "")
        print("[SCHEMA] Loaded schematizer prompts")
    except Exception as exc:
        print(f"[SCHEMA] Failed to load schematizer prompts: {exc}")
        pre = ""
        post = ""

    schema_text = ""
    if Path(schema_file).is_file():
        try:
            schema_text = Path(schema_file).read_text(encoding="utf-8")
            print(f"[SCHEMA] Loaded schema template from {schema_file}")
        except Exception as exc:
            print(f"[SCHEMA] Failed to read schema file: {exc}")

    prompt = f"{pre}\n{schema_text.strip()}\n\n{post}".strip()
    print(f"[SCHEMA] Prompt length: {len(prompt)} characters")

    client = openai.OpenAI()
    messages = [
        {"role": "system", "content": prompt},
        {"role": "user", "content": first_para},
    ]

    try:
        resp = client.chat.completions.create(
            model=THINKING_MODEL,
            messages=messages,
            max_completion_tokens=20000,
        )
        row = resp.choices[0].message.content
        print("[SCHEMA] Received schema from LLM")
    except Exception as exc:
        print(f"[SCHEMA] LLM schematization failed: {exc}")
        row = ""

    out_path = exp_path
    name = out_path.name
    if name.endswith(".exp.txt"):
        name = name[: -len(".exp.txt")]
    elif name.endswith(".txt"):
        name = name[: -len(".txt")]
    elif name.endswith(".exp"):
        name = name[: -len(".exp")]
    out_path = out_path.with_name(name + ".schema.txt")
    try:
        out_path.write_text(row, encoding="utf-8")
        print(f"[SCHEMA] Saved schema to {out_path}")
    except Exception as exc:
        print(f"[SCHEMA] Failed to save schema text: {exc}")

    return row


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

    txt_path = pdf_path.with_suffix(".txt")
    tesseract = shutil.which("tesseract")
    pdftoppm = shutil.which("pdftoppm")
    if not tesseract or not pdftoppm:
        print(
            "[OCR] Required OCR tools missing; attempting direct text extraction fallback."
        )
        return _ocr_pdf_fallback(pdf_path, txt_path)

    tmpdir = tempfile.mkdtemp(prefix="ocr_")
    print(f"[OCR] Temporary directory for image pages: {tmpdir}")
    try:
        print(f"[OCR] Running pdftoppm to convert PDF pages to images...")
        subprocess.run(
            [
                "pdftoppm",
                "-q",
                str(pdf_path),
                str(Path(tmpdir) / "page"),
                "-png",
            ],
            check=True,
            stderr=subprocess.DEVNULL,
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

        cleaned_text = _cleanup_ocr_text(raw_text)

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
