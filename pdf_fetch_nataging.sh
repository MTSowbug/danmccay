#!/usr/bin/env bash
# pdf_fetch_nataging.sh — download a Nature Aging PDF using institutional cookies

set -euo pipefail

if [ "$#" -ne 1 ]; then
  printf 'Usage: %s <doi-suffix | full-doi>\n' "$0" >&2
  printf 'Examples:\n  %s s43587-025-00901-6\n  %s 10.1038/s43587-025-00901-6\n' "$0" "$0" >&2
  exit 1
fi

raw="$1"

# Allow optional scheme and "doi.org/" prefix like pdf_fetch_aging.sh
arg="${raw#http://}"
arg="${arg#https://}"
arg="${arg#doi.org/}"

if [[ "$arg" == */* ]]; then
  DOI="$arg"            # already a full DOI
else
  DOI="10.1038/$arg"    # prepend Nature prefix
fi

suffix="${DOI#10.1038/}"
out="${DOI//\//_}.pdf"

script_dir="$(cd "$(dirname "$0")" && pwd)"
COOKIE_JAR="${script_dir}/../pdfs/jar.cookies"

touch "$COOKIE_JAR"

wget -O "$out" \
  --load-cookies "$COOKIE_JAR" \
  --save-cookies "$COOKIE_JAR" \
  --keep-session-cookies \
  --user-agent='Mozilla/5.0 (X11; Linux x86_64; rv:126.0) Gecko/20100101 Firefox/126.0' \
  "https://www.nature.com/articles/${suffix}.pdf"

