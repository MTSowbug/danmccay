#!/usr/bin/env bash
###############################################################################
#  Fetch “Targeting GRPR for sex hormone-dependent cancer after loss of E-cadherin”
#  PubMed record: 40500450
#  If institutional cookies are available (jar.cookies) they will be used.
#  The script tries, in order of preference, the publisher’s PDF, the SpringerLink
#  mirror, and an earlier BioRxiv pre-print.  Every step prints diagnostics.
###############################################################################
set -euo pipefail

PUBMED_ID=40500450
PUBMED_URL="https://pubmed.ncbi.nlm.nih.gov/${PUBMED_ID}/"
COOKIE_JAR="jar.cookies"

# Ensure cookie file exists (it may be empty)
touch "${COOKIE_JAR}"

################################################################################
# Step 1 – discover DOI from PubMed (robust to HTML layout changes)
################################################################################
echo ">>> Retrieving DOI from PubMed ${PUBMED_ID}"
DOI=$(
  curl -Ls --cookie "${COOKIE_JAR}" --cookie-jar "${COOKIE_JAR}" "${PUBMED_URL}" \
  | grep -Eo 'doi: *10\.[0-9]+/[A-Za-z0-9._-]+' \
  | head -n1 \
  | sed -E 's/^doi: *//'
)
echo ">>> Found DOI: ${DOI:-<none>}"
if [[ -z "${DOI}" ]]; then
  echo "!!! Unable to locate DOI – aborting." >&2
  exit 1
fi

################################################################################
# Helper: attempt a download, verify it is a PDF, rename on success
################################################################################
download_pdf () {
  local url="$1"               # URL to fetch
  local target="$2"            # Desired filename
  local tmp="__tmp_${RANDOM}.dat"

  echo ">>> Trying: ${url}"
  # Use -L to follow redirects; save/refresh cookies; timeout if server stalls.
  if curl -fL --retry 3 --max-time 120 \
         --cookie "${COOKIE_JAR}" --cookie-jar "${COOKIE_JAR}" \
         -A "Mozilla/5.0 (X11; Linux x86_64)" \
         -o "${tmp}" "${url}"; then

      # Verify signature – first four bytes of any PDF are “%PDF”
      if head -c 4 "${tmp}" | grep -q "%PDF"; then
        mv "${tmp}" "${target}"
        echo ">>> SUCCESS – saved as ${target}"
        return 0
      else
        echo ">>> Downloaded file is NOT a PDF – discarding."
      fi
  else
      echo ">>> HTTP request failed (status $?)."
  fi
  rm -f "${tmp}"
  return 1
}

################################################################################
# Step 2 – attempt publisher (Nature) PDF
################################################################################
# DOI suffix after “10.1038/”
suffix="${DOI#10.1038/}"
NATURE_PDF="https://www.nature.com/articles/${suffix}.pdf"

if download_pdf "${NATURE_PDF}" "article_fulltest_version1.pdf"; then
  exit 0
fi

################################################################################
# Step 3 – attempt SpringerLink PDF mirror
################################################################################
SPRINGER_PDF="https://link.springer.com/content/pdf/${DOI}.pdf"

if download_pdf "${SPRINGER_PDF}" "article_fulltest_version1.pdf"; then
  exit 0
fi

################################################################################
# Step 4 – fall back to the closest freely-available version (BioRxiv pre-print)
# NOTE: Version/date may differ slightly from the final Nature paper.
################################################################################
BIORXIV_PDF="https://www.biorxiv.org/content/10.1101/2022.12.02.518844v2.full.pdf"

if download_pdf "${BIORXIV_PDF}" "article_fulltest_version1.pdf"; then
  echo ">>> WARNING – pre-print downloaded; final Nature PDF still unavailable."
  exit 0
fi

################################################################################
echo "!!! All download attempts failed.  See log above for details."
exit 1
