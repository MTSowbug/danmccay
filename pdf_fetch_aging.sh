#!/usr/bin/env bash
# pdf_fetch.sh — fetch an open-access PDF by DOI or DOI-URL
# Usage: ./pdf_fetch.sh 10.18632/aging.206245
#        ./pdf_fetch.sh doi.org/10.18632/aging.206245
#        ./pdf_fetch.sh https://doi.org/10.18632/aging.206245

set -euo pipefail

[ "$#" -eq 1 ] || { echo "Usage: $0 <doi or doi-url>"; exit 1; }

raw="$1"

# Strip optional scheme and “doi.org/” host, leaving only the DOI proper
doi="${raw#http://}"
doi="${doi#https://}"
doi="${doi#doi.org/}"

# doi now looks like “10.18632/aging.206245”
case "$doi" in
  10.*/*) ;;                               # looks OK
  *) echo "Argument does not look like a DOI: $raw" >&2; exit 1 ;;
esac

out="${doi//\//_}.pdf"                     # e.g. 10.18632_aging.206245.pdf
tmp=$(mktemp)

# 1. Try normal DOI content negotiation for PDF
ctype=$(curl -Ls -H 'Accept: application/pdf' \
               -o "$tmp" -w '%{content_type}' \
               "https://doi.org/$doi" || true)

if [[ "$ctype" == application/pdf* ]]; then
  mv "$tmp" "$out"
  echo "saved $out (via resolver)"
  exit 0
fi
rm -f "$tmp"                               # Not a PDF → fall back

# 2. Publisher-specific fall-backs
case "$doi" in
  10.18632/*)                              # Impact Journals (Aging, Oncotarget …)
      id=${doi#10.18632/*}                 # aging.206245 → still whole, need numeric
      id=${id#*.}                          # keep part after last dot → 206245
      pdf="https://www.aging-us.com/article/${id}/pdf"
      ;;
  10.1111/*|10.1002/*)                     # Wiley prefixes
      pdf="https://onlinelibrary.wiley.com/doi/pdfdirect/${doi}?download=true"
      ;;
  *) echo "No fallback rule for prefix ${doi%%/*}" >&2; exit 1 ;;
esac

wget -q --show-progress -O "$out" "$pdf"
echo "saved $out (via fallback)"
