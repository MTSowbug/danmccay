#!/usr/bin/env bash
# pdf_fetch_tcr.sh — download a Translational Cancer Research PDF using institutional cookies

set -euo pipefail

COOKIE_JAR="../pdfs/jar.cookies"

if [ "$#" -ne 1 ]; then
  printf 'Usage: %s <doi-suffix | full-doi>\n' "$0" >&2
  printf 'Examples:\n  %s https://doi.org/10.21037/tcr-2024-2279 %s tcr-2024-2279\n' "$0" "$0" >&2
  exit 1
fi

raw="$1"

# Remove any scheme and leading doi.org/ so we’re left with the bare DOI or suffix
raw="${raw#http://}"
raw="${raw#https://}"
raw="${raw#doi.org/}"

# Build the complete DOI suffix
if [[ "$raw" == */* ]]; then
  doi_suffix="$raw"            # full DOI already supplied
else
  doi_suffix="10.21037/$raw"   # prepend journal prefix
fi

# Final, canonical DOI URL
DOI_URL="https://doi.org/$doi_suffix"
# --------------------------------------------------------------------
# 1. Resolve DOI → final landing page
# --------------------------------------------------------------------
echo "==> Resolving DOI: $DOI_URL"
LANDING_URL="$(curl -Ls -o /dev/null -w '%{url_effective}' "$DOI_URL")"
echo "    Landed on: $LANDING_URL"      # expected: https://tcr.amegroups.org/article/view/100372/html :contentReference[oaicite:0]{index=0}

# --------------------------------------------------------------------
# 2. Derive the “/pdf” viewer page from the landing URL
# --------------------------------------------------------------------
echo "==> Deriving /pdf page"
if [[ "$LANDING_URL" =~ /html$ ]]; then
  PDF_PAGE_URL="${LANDING_URL%/html}/pdf"
else
  echo "WARNING: Landing URL did not end with /html; attempting generic substitution"
  PDF_PAGE_URL="${LANDING_URL}/pdf"
fi
echo "    PDF page candidate: $PDF_PAGE_URL"   # e.g. https://tcr.amegroups.org/article/view/100372/pdf :contentReference[oaicite:1]{index=1}

# --------------------------------------------------------------------
# 3. Scrape the direct /article/download/… link
# --------------------------------------------------------------------
echo "==> Scraping direct PDF link from PDF page"
PDF_DOWNLOAD_PATH="$(curl -L -b "$COOKIE_JAR" -c "$COOKIE_JAR" -s "$PDF_PAGE_URL" \
                   | grep -Eo '/article/download/[0-9]+/[0-9]+' \
                   | head -n1 || true)"
if [[ -n "$PDF_DOWNLOAD_PATH" ]]; then
  PDF_URL="https://tcr.amegroups.org${PDF_DOWNLOAD_PATH}"
  echo "    Found direct PDF URL: $PDF_URL"    # e.g. https://tcr.amegroups.org/article/download/100372/74317 :contentReference[oaicite:2]{index=2}
else
  echo "    No /article/download/… link found – will try the viewer page itself"
  PDF_URL="$PDF_PAGE_URL"
fi

# --------------------------------------------------------------------
# 4. Attempt downloads
# --------------------------------------------------------------------
echo "==> Attempting primary download with institutional cookies"
if curl -L -b "$COOKIE_JAR" -c "$COOKIE_JAR" -f -o article_fulltest_version1.pdf "$PDF_URL"; then
  echo "SUCCESS: Saved as article_fulltest_version1.pdf"
  exit 0
else
  echo "Primary download failed – headers or authentication may have blocked it."
fi

echo "==> Attempting fallback download WITHOUT cookies"
if curl -L -f -o article_fulltest_version2.pdf "$PDF_URL"; then
  echo "SUCCESS (anonymous): Saved as article_fulltest_version2.pdf"
  exit 0
else
  echo "Both attempts failed. Inspect curl output above and confirm network, cookie, or access issues."
  exit 1
fi