#!/usr/bin/env bash
# ===============================================================
# Fetch the full-text PDF for an Aging Cell article
# ===============================================================
# Requirements:
#   * curl, grep, sed, awk, file, ls
#   * Optional cookie jar “jar.cookies” in the working directory.
# Debugging information is echoed at every step.
# ===============================================================

set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "Usage: $0 acel_70123" >&2
  exit 1
fi

# Argument should look like "acel_70123" (underscore instead of dot)
SUFFIX="${1//./_}"

PMID=""
DOI="10.1111/${SUFFIX//_/.}"
BASE_NAME="article_fulltest_version"
COOKIE_JAR="jar.cookies"

# -----------------------------------------------------------------
# Step 1  Resolve the DOI and capture the landing page
# -----------------------------------------------------------------
echo ">>> Resolving DOI and saving landing page …"
curl -L \
     -c "${COOKIE_JAR}" \
     -b "${COOKIE_JAR}" \
     -A "Mozilla/5.0 (X11; Linux x86_64)" \
     -o doi_landing.html \
     "https://doi.org/${DOI}" \
     2>&1 | tee curl_doi.log

echo ">>> DOI resolution complete (see doi_landing.html)."

# -----------------------------------------------------------------
# Step 2  Scrape candidate PDF links from the landing page
# -----------------------------------------------------------------
echo ">>> Searching landing page for PDF links …"
grep -Eoi 'href="[^"]+\.pdf[^"]*"' doi_landing.html \
  | sed -E 's/href="([^"]+)".*/\1/' \
  | sort -u > pdf_links_found.txt

cat pdf_links_found.txt

PDF_URL=$(head -n 1 pdf_links_found.txt || true)

# -----------------------------------------------------------------
# Step 3  If no PDF link found, try known Wiley patterns
# -----------------------------------------------------------------
if [[ -z "$PDF_URL" ]]; then
  echo ">>> No direct PDF link found. Trying common Wiley URL patterns …"
  declare -a CANDIDATES=(
    "https://onlinelibrary.wiley.com/doi/pdfdirect/${DOI}"
    "https://onlinelibrary.wiley.com/doi/pdf/${DOI}"
    "https://onlinelibrary.wiley.com/doi/epdf/${DOI}"
  )
  for URL in "${CANDIDATES[@]}"; do
    echo ">>> Testing ${URL}"
    STATUS=$(curl -L -s -o /dev/null -w '%{http_code}' "$URL")
    echo "    HTTP ${STATUS}"
    if [[ "$STATUS" == "200" ]]; then
      PDF_URL="$URL"
      break
    fi
  done
fi

# -----------------------------------------------------------------
# Step 4  Abort if no URL determined
# -----------------------------------------------------------------
if [[ -z "$PDF_URL" ]]; then
  echo "ERROR: Unable to determine a valid PDF URL. Inspect logs for details." >&2
  exit 1
fi

echo ">>> PDF URL resolved: ${PDF_URL}"

# -----------------------------------------------------------------
# Step 5  Download the PDF
# -----------------------------------------------------------------
OUTFILE="${BASE_NAME}1.pdf"
echo ">>> Downloading PDF to ${OUTFILE} …"
curl -L \
     -C - \
     -c "${COOKIE_JAR}" \
     -b "${COOKIE_JAR}" \
     --retry 3 \
     -A "Mozilla/5.0 (X11; Linux x86_64)" \
     -o "${OUTFILE}" \
     "${PDF_URL}" \
     2>&1 | tee download.log

# -----------------------------------------------------------------
# Step 6  Validate download
# -----------------------------------------------------------------
echo ">>> Verifying downloaded file …"
file "${OUTFILE}"
ls -lh "${OUTFILE}"

echo ">>> Done. If the file appears invalid, inspect download.log and try another candidate URL."
