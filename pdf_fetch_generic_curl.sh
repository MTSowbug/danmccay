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



# ./curl_ff109 --impersonate ff109 $arg \
#   -o "$out" \
#  -b "$COOKIE_JAR" \
#  -c "$COOKIE_JAR_OUT" \
#  -L --max-redirs 10 \
#  --proto =https --proto-redir =https \
#  -e $arg \


# Find the directory of this script
dir=${0%/*}

# The list of ciphers can be obtained by looking at the Client Hello message in
# Wireshark, then converting it using the cipherlist array at
# https://github.com/curl/curl/blob/master/lib/vtls/nss.c
"$dir/curl-impersonate-ff" \
    --ciphers aes_128_gcm_sha_256,chacha20_poly1305_sha_256,aes_256_gcm_sha_384,ecdhe_ecdsa_aes_128_gcm_sha_256,ecdhe_rsa_aes_128_gcm_sha_256,ecdhe_ecdsa_chacha20_poly1305_sha_256,ecdhe_rsa_chacha20_poly1305_sha_256,ecdhe_ecdsa_aes_256_gcm_sha_384,ecdhe_rsa_aes_256_gcm_sha_384,ecdhe_ecdsa_aes_256_sha,ecdhe_ecdsa_aes_128_sha,ecdhe_rsa_aes_128_sha,ecdhe_rsa_aes_256_sha,rsa_aes_128_gcm_sha_256,rsa_aes_256_gcm_sha_384,rsa_aes_128_sha,rsa_aes_256_sha \
    -H 'User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/109.0' \
    -H 'Accept: text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8' \
    -H 'Accept-Language: en-US,en;q=0.5' \
    -H 'Accept-Encoding: gzip, deflate, br' \
    -H 'Upgrade-Insecure-Requests: 1' \
    -H 'Sec-Fetch-Dest: document' \
    -H 'Sec-Fetch-Mode: navigate' \
    -H 'Sec-Fetch-Site: none' \
    -H 'Sec-Fetch-User: ?1' \
    -H 'TE: Trailers' \
    -b "$COOKIE_JAR" \
    -c "$COOKIE_JAR_OUT" \
    -L --max-redirs 10 \
    -e "$arg" \
    -o "$out" \
    --http2 --compressed \
    "$@"
