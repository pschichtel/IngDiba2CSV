#!/usr/bin/env bash

here="$(dirname "$(readlink -f "$0")")"
source="$(pwd)"
target="${source}/transactions.csv"
rm "$target"
tmp="$(mktemp -d)"
cd "$tmp" || exit
for f in "${source}"/Girokonto_*_Kontoauszug_*.pdf
do
    base="$(basename "$f")"
    tmpf="$tmp/$base"
    cp "$f" "$tmpf"
    pdftohtml "$tmpf"
    python3 "${here}/html2csv.py" "${tmpf%.*}s.html" >> "$target"
done
rm -R "$tmp"
