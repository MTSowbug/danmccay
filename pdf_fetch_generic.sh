#!/usr/bin/env bash
# pdf_fetch_generic.sh — download a webpage or PDF using institutional cookies

set -euo pipefail

arg="${1:?usage: $0 <url>}"

out="tempfile"

script_dir="$(cd "$(dirname "$0")" && pwd)"
COOKIE_JAR_DEFAULT="${script_dir}/../pdfs/jar.cookies"
COOKIE_JAR_DEFAULT_OUT="${script_dir}/../pdfs/jar.cookies.out"
COOKIE_JAR="${PDF_FETCH_COOKIE_JAR:-$COOKIE_JAR_DEFAULT}"
COOKIE_JAR_OUT="${PDF_FETCH_COOKIE_JAR_OUT:-$COOKIE_JAR_DEFAULT_OUT}"



wget -O "$out" \
  --load-cookies "$COOKIE_JAR" \
  --save-cookies "$COOKIE_JAR_OUT" \
  --keep-session-cookies \
  --user-agent='Mozilla/5.0 (X11; Linux x86_64; rv:126.0) Gecko/20100101 Firefox/129.0' \
  --header='Accept: text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8' \
  --header='Accept-Language: en-US,en;q=0.9' \
  --header='Upgrade-Insecure-Requests: 1' \
  --header='Sec-Fetch-Site: same-origin' \
  --header='Sec-Fetch-Mode: navigate' \
  --header='Sec-Fetch-Dest: document' \
  --compression=auto \
  --max-redirect=10 \
  --https-only \
  "$arg"
