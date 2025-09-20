#!/usr/bin/env bash
# pdf_fetch_geroscience.sh — download a GeroScience PDF using institutional cookies

set -euo pipefail

arg="$1"

out="tempfile"

script_dir="$(cd "$(dirname "$0")" && pwd)"
COOKIE_JAR_DEFAULT="${script_dir}/../pdfs/jar.cookies"
COOKIE_JAR="${PDF_FETCH_COOKIE_JAR:-$COOKIE_JAR_DEFAULT}"

touch "$COOKIE_JAR"

wget -O "$out" \
  --load-cookies "$COOKIE_JAR" \
  --save-cookies "$COOKIE_JAR" \
  --keep-session-cookies \
  --user-agent='Mozilla/5.0 (X11; Linux x86_64; rv:126.0) Gecko/20100101 Firefox/126.0' \
  $arg

