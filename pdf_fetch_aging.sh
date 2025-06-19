#!/usr/bin/env bash
# pdf_fetch.sh — fetch an open-access PDF by DOI
set -euo pipefail

[ "$#" -eq 1 ] || { echo "Usage: $0 <doi>"; exit 1; }
doi="$1"
out="${doi//\//_}.pdf"          # 10.18632_aging.206245.pdf

tmp=$(mktemp)
ctype=$(curl -Ls -H 'Accept: application/pdf' \
               -o "$tmp" -w '%{content_type}' \
               "https://doi.org/$doi" || true)

if [[ "$ctype" == application/pdf* ]]; then        # Success via content negotiation
    mv "$tmp" "$out"
    echo "saved $out"
    exit 0
fi
rm -f "$tmp"                                       # Not a PDF → fall back

case "$doi" in
    10.18632/*)                                    # Impact Journals (Aging, Oncotarget…)
        art=${doi#10.18632/}                       # aging.206245
        id=${art#*.}                               # 206245
        pdf="https://www.aging-us.com/article/${id}/pdf"
        wget -q --show-progress -O "$out" "$pdf"
        ;;
    10.1111/*|10.1002/*)                           # Wiley prefixes
        wget -q --show-progress -O "$out" \
             "https://onlinelibrary.wiley.com/doi/pdfdirect/${doi}?download=true"
        ;;
    *) echo "No fallback rule for prefix ${doi%%/*}" >&2; exit 1 ;;
esac

echo "saved $out"
