#!/usr/bin/env bash
# pdf_fetch_geroscience.sh — download a GeroScience PDF using institutional cookies

set -euo pipefail

if [ "$#" -ne 1 ]; then
  printf 'Usage: %s <doi-suffix | full-doi>\n' "$0" >&2
  printf 'Examples:\n  %s s11357-024-00434-5\n  %s 10.1007/s11357-024-00434-5\n' "$0" "$0" >&2
  exit 1
fi

raw="$1"

# Strip optional scheme and "doi.org/" prefix
arg="${raw#http://}"
arg="${arg#https://}"
arg="${arg#doi.org/}"

# Prepend the Springer prefix only when missing
if [[ "$arg" == */* ]]; then
  DOI="$arg"
else
  DOI="10.1007/$arg"
fi

out="${DOI//\//_}.pdf"

script_dir="$(cd "$(dirname "$0")" && pwd)"
COOKIE_JAR="${script_dir}/../pdfs/jar.cookies"

touch "$COOKIE_JAR"

wget -O "$out" \
  --load-cookies "$COOKIE_JAR" \
  --save-cookies "$COOKIE_JAR" \
  --keep-session-cookies \
  --user-agent='Mozilla/5.0 (X11; Linux x86_64; rv:126.0) Gecko/20100101 Firefox/126.0' \
  "https://link.springer.com/content/pdf/${DOI}.pdf"

