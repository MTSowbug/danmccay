#!/usr/bin/env bash
# pdf_fetch_agingcell.sh — download an open-access Wiley PDF by DOI

set -euo pipefail

if [ "$#" -ne 1 ]; then
  printf 'Usage: %s <doi-suffix | full-doi>\n' "$0" >&2
  printf 'Examples:\n  %s acel.70110\n  %s 10.1111/acel.70110\n' "$0" "$0" >&2
  exit 1
fi

arg="$1"

# Prepend the Wiley prefix only when it’s missing
if [[ "$arg" == */* ]]; then
  DOI="$arg"            # already a full DOI
else
  DOI="10.1111/$arg"    # add Wiley prefix
fi

out="${DOI//\//_}.pdf"   # e.g. 10.1111_acel.70110.pdf

wget -O "$out" \
  --header='Accept: text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8' \
  --header='Accept-Language: en-US,en;q=0.5' \
  --referer="https://onlinelibrary.wiley.com/doi/${DOI}" \
  --user-agent='Mozilla/5.0 (X11; Linux x86_64; rv:126.0) Gecko/20100101 Firefox/126.0' \
  "https://onlinelibrary.wiley.com/doi/pdfdirect/${DOI}?download=true"

