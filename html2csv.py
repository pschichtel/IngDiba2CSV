#!/usr/bin/env python3
import os
import html
import shutil
import sys
import re
import subprocess
import tempfile
import typing
from decimal import *
from os.path import basename, realpath
import decimal

kinds = {
    'Lastschrift': 'Lastschrift',
    'Gehalt/Rente': 'Gehalt/Rente',
    'Ueberweisung': 'Überweisung',
    'Dauerauftrag/Terminueberw.': 'Dauerauftrag / Terminueberweisung',
    'Gutschrift': 'Gutschrift',
    'Abschluss': 'Abschluss',
    'Abbuchung': 'Abbuchung',
    'Gutschrift/Dauerauftrag': 'Gutschrift / Dauerauftrag',
    'Retoure': 'Retoure',
    'Wertpapierkauf': 'Wertpapierkauf',
    'Zins/Dividende WP': 'Zins/Dividende WP',
}
internal_transaction_kinds = {'Wertpapierkauf', 'Abschluss', 'Zins/Dividende WP'}
nbsp = html.unescape('&#160;')

def nbsp_to_sp(s: str) -> str:
    return re.sub(nbsp, ' ', s)

def is_internal_transaction(e: dict[str, any]) -> bool:
    if 'kind' in e:
        return e['kind'] in internal_transaction_kinds
    return False


def parse_date(date) -> str | None:
    dates = re.findall('^(\\d\\d)\\.(\\d\\d)\\.(\\d\\d\\d\\d)$', date)
    if len(dates) != 1:
        return None
    else:
        (d, m, y) = dates[0]
        return "{}-{}-{}".format(y, m, d)


def preprocess_part(part: str) -> str:
    nbsp_stripped = part.replace('&#160;', ' ')
    html_stripped = re.sub('<[^>]+>', '', nbsp_stripped)
    return html_stripped.strip()


def chunk_entry(entry: str) -> list[str]:
    processed = [preprocess_part(s) for part in entry.split('</b>') for s in part.split("<br/>")]
    return [s for s in processed if len(s) > 0]


def parse_entry(chunk: str) -> dict[str, any]:
    parsed = dict()

    extract_kind(chunk, parsed)
    extract_initiation(chunk, parsed)
    extract_valuta(chunk, parsed)
    extract_partner(chunk, parsed)
    extract_amount(chunk, parsed)
    extract_application(chunk, parsed)
    extract_reference(chunk, parsed)
    extract_mandate(chunk, parsed)
    
    if 'valuta' in parsed and 'initiation' not in parsed:
        parsed['initiation'] = parsed['valuta']
    
    if 'initiation' in parsed and 'valuta' not in parsed:
        parsed['valuta'] = parsed['initiation']
    
    # parsed['raw'] = entry
    return parsed


def extract_initiation(chunk: str, parsed: dict[str, any]) -> None:
    match = re.search('<br/>', chunk, re.IGNORECASE)
    assert match is not None
    date = parse_date(chunk[0:match.span()[0]])
    assert date is not None
    parsed['initiation'] = date


def extract_valuta(chunk: str, parsed: dict[str, any]) -> None:
    lines = chunk.split('\n')
    assert len(lines) > 3, "Entries are expected to have at least four lines"
    match = re.search('([^<]+)<br/>', lines[3], re.IGNORECASE)
    assert match is not None
    date = parse_date(match.group(1))
    assert date is not None
    parsed['valuta'] = date


def extract_application(chunk: str, parsed: dict[str, any]) -> None:
    if parsed['kind'] == 'Abschluss':
        return

    lines = chunk.split('\n')
    if len(lines) > 4:
        line = lines[4]
        line = re.sub('\r?\n', '', line)
        line = re.sub('(?i)<br/>', '\n', line)
        lines = [nbsp_to_sp(html.unescape(s)) for s in line.strip().split('\n') if not s.startswith('Mandat:') and not s.startswith('Referenz:')]
        application = ' '.join(lines)
        parsed['application'] = application


def extract_reference(chunk: str, parsed: dict[str, any]) -> None:
    match = re.search('<br/>\n?Referenz:&#160;([^<\\s]+)<br/>$', chunk, re.IGNORECASE)
    if match is not None:
        parsed['reference'] = nbsp_to_sp(html.unescape(match.group(1)).strip())


def extract_mandate(chunk: str, parsed: dict[str, any]) -> None:
    match = re.search('<br/>\n?Mandat:&#160;([^<\\s]+)<br/>', chunk, re.IGNORECASE)
    if match is not None:
        parsed['mandate'] = nbsp_to_sp(html.unescape(match.group(1)).strip())


def extract_kind(chunk: str, parsed: dict[str, any]) -> None:
    match = re.search('<b>([^<]+)</b>', chunk, re.IGNORECASE)
    assert match is not None, "Entries are expected to have a kind enclosed in <b>"

    kind = html.unescape(match.group(1)).strip()
    if kind in kinds:
        parsed['kind'] = kinds[kind]
    else:
        parsed['kind'] = kind


def extract_partner(chunk: str, parsed: dict[str, any]) -> None:
    if is_internal_transaction(parsed):
        parsed['partner'] = 'ING-DiBa'
    else:
        match = re.search('</b>([^<]+)<br/>', chunk, re.IGNORECASE)
        assert match is not None, "Entries are expected to have the transaction partner right after the kind!"
        parsed['partner'] = html.unescape(match.group(1)).strip()


def extract_amount(chunk: str, parsed: dict[str, any]) -> None:
    lines = chunk.split('\n')
    assert len(lines) >= 3, "Entries are expected to have at least three lines"
    parsed['amount'] = number_to_decimal(lines[2])


def extract_saldos(content: str) -> tuple[Decimal, Decimal]:
    raw_saldos = re.findall('<b>(?:Neuer|Alter)\\s+Saldo</b><br/>[^<]*<b>(\\S+)\\s+Euro</b>', content)
    return number_to_decimal(raw_saldos[0]), number_to_decimal(raw_saldos[1])


def number_to_decimal(number) -> Decimal:
    format_normalized = number.replace('.', '').replace(',', '.')
    stripped = re.sub('(\\.\\d\\d).*', '\\1', format_normalized)
    try:
        return Decimal(stripped)
    except decimal.InvalidOperation:
        return Decimal(0)


def dejunk(content: str) -> str:
    #content = re.sub('<hr/>\\s*<a[^>]+>[\\s\\S]+?(<img[^>]+><br/>\\s*)+.+?<br/>\\s*', '', content)
    content = re.sub('<hr/>\\s*', '', content)
    content = re.sub('<img[^>]+><br/>\\s*', '', content)
    content = re.sub('<b>Abschlussbetrag[\\S\\s]+', '', content)
    content = re.sub('^.+?_T<br/>\\s*', '', content, flags=re.MULTILINE)
    
    return content


def resolve_and_validate_saldos(old_saldo: Decimal, new_saldo: Decimal, transactions: list[dict[str, any]]) -> list[dict[str, any]]:
    saldo = old_saldo
    for i in range(0, len(transactions)):
        t = transactions[i]
        t['old_saldo'] = saldo
        if 'amount' in t:
            saldo += t['amount']
            t['new_saldo'] = saldo
    if saldo != new_saldo:
        m = 'Expected new saldo is {}, calculated saldo is {}. It seems that not all entries were extracted.'
        raise ValueError(m.format(new_saldo, saldo))
    return transactions


def process_html(html_path: str) -> tuple[Decimal, Decimal, list[dict[str, any]]]:
    with open(html_path, 'r') as content_file:
        content = content_file.read()

    dejunked = dejunk(content)

    old_saldo, new_saldo = extract_saldos(content)

    pages = re.split('<a name=\\d+></a>', dejunked)
    all_transactions = []
    for page in pages:
        r = r'\d\d\.\d\d\.\d\d\d\d[^\n]*\n<b>[^\n]+\n[^\n]+\n\d\d.\d\d.\d\d\d\d[^\n]+(?:\n(?!\d\d\.\d\d\.\d\d\d\d)[^\n]+)?'
        table_entries: list[str] = re.findall(r, page)
        chunks = [chunk for chunk in table_entries]
        parsed_entries = [parse_entry(c) for c in chunks]
        transactions = [e for e in parsed_entries if 'kind' in e and 'amount' in e and 'initiation' in e]
        all_transactions.extend(transactions)
    return old_saldo, new_saldo, all_transactions


def convert_pdf(pdf_path: str, tmp_dir: str) -> str:
    pdf_filename = basename(pdf_path)
    no_ext = os.path.splitext(pdf_filename)[0]
    output_path = os.path.join(tmp_dir, no_ext + ".html")
    html_path = os.path.join(tmp_dir, no_ext + "s.html")

    # subprocess.run(executable="pdftohtml", args=[pdf_path], cwd=output_dir)
    subprocess.call(["pdftohtml", pdf_path, output_path], cwd=tmp_dir, stdout=sys.stderr)
    return html_path


def emit_csv(transactions: list[dict[str, any]]) -> None:
    for entry in transactions:
        print('"{}","{}","{}","{}","{}",{},{},{},"{}","{}"'.format(entry.get('initiation', ''),
                                                                   entry.get('valuta', ''),
                                                                   entry.get('partner', ''),
                                                                   entry.get('kind', ''),
                                                                   entry.get('application', ''),
                                                                   entry.get('old_saldo', ''),
                                                                   entry.get('new_saldo', ''),
                                                                   entry.get('amount', ''),
                                                                   entry.get('reference', ''),
                                                                   entry.get('mandate', '')))

T = typing.TypeVar('T')
def flatten(l: list[list[T]]) -> list[T]:
    return [item for sublist in l for item in sublist]


def convert_all_pdfs() -> None:
    tmp_path = tempfile.mkdtemp(prefix="pdftohtml_")

    pdf_paths = [realpath(sys.argv[i]) for i in range(1, len(sys.argv))]
    html_paths = [convert_pdf(p, tmp_path) for p in pdf_paths]
    parsed_docs = [process_html(p) for p in html_paths]

    final_transactions = flatten([resolve_and_validate_saldos(old, new, doc) for old, new, doc in parsed_docs])
    emit_csv(final_transactions)

    shutil.rmtree(tmp_path)


convert_all_pdfs()
