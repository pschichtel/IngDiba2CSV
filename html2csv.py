#!/usr/bin/env python3
import os
import shutil
import sys
import re
import subprocess
import tempfile
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
    'Wertpapierkauf': 'Wertpapierkauf'
}
internal_transaction_kinds = {'Wertpapierkauf', 'Abschluss'}


def is_internal_transaction(e):
    if 'kind' in e:
        return e['kind'] in internal_transaction_kinds
    return False


def parse_date(date):
    dates = re.findall('^(\\d\\d)\\.(\\d\\d)\\.(\\d\\d\\d\\d)$', date)
    if len(dates) != 1:
        return None
    else:
        (d, m, y) = dates[0]
        return "{}-{}-{}".format(y, m, d)


def preprocess_part(part):
    nbsp_stripped = part.replace('&#160;', ' ')
    html_stripped = re.sub('<[^>]+>', '', nbsp_stripped)
    return html_stripped.strip()


def chunk_entry(entry):
    processed = [preprocess_part(s) for part in entry.split('</b>') for s in part.split("<br/>")]
    return [s for s in processed if len(s) > 0]


def parse_entry(entry):
    parsed = dict()

    extract_kind(entry, parsed)
    extract_initiation(entry, parsed)
    extract_valuta(entry, parsed)
    extract_partner(entry, parsed)
    extract_amount(entry, parsed)
    extract_application(entry, parsed)
    extract_reference(entry, parsed)
    extract_mandate(entry, parsed)
    
    if 'valuta' in parsed and 'initiation' not in parsed:
        parsed['initiation'] = parsed['valuta']
    
    if 'initiation' in parsed and 'valuta' not in parsed:
        parsed['valuta'] = parsed['initiation']
    
    # parsed['raw'] = entry
    return parsed


def extract_initiation(raw, parsed):
    if len(raw) > 0:
        date = parse_date(raw[0])
        if date is not None:
            parsed['initiation'] = date


def extract_valuta(raw, parsed):
    index = 4
    if is_internal_transaction(parsed):
        index = 3
    if len(raw) > index:
        date = parse_date(raw[index])
        if date is not None:
            parsed['valuta'] = date


def extract_application(raw, parsed):
    if len(raw) > 5:
        parsed['application'] = raw[5]


def extract_reference(raw, parsed):
    for p in raw:
        refs = re.findall('^Referenz:\\s*(.+)$', p)
        if len(refs) == 1:
            parsed['reference'] = refs[0]
            return


def extract_mandate(raw, parsed):
    for p in raw:
        refs = re.findall('^Mandat:\\s*(.+)$', p)
        if len(refs) == 1:
            parsed['mandate'] = refs[0]
            return


def extract_kind(raw, parsed):
    if len(raw) > 1:
        kind = raw[1]
        if kind in kinds:
            parsed['kind'] = kinds[kind]
        else:
            parsed['kind'] = kind


def extract_partner(raw, parsed):
    if is_internal_transaction(parsed):
        parsed['partner'] = 'ING-DiBa'
    else:
        parsed['partner'] = raw[2]


def extract_amount(raw, parsed):
    index = 3
    if is_internal_transaction(parsed):
        index = 2
    if len(raw) > index:
        parsed['amount'] = number_to_decimal(raw[index])


def extract_saldos(content):
    raw_saldos = re.findall('<b>(?:Neuer|Alter)\\s+Saldo</b><br/>[^<]*<b>(\\S+)\\s+Euro</b>', content)
    return number_to_decimal(raw_saldos[0]), number_to_decimal(raw_saldos[1])


def number_to_decimal(number):
    format_normalized = number.replace('.', '').replace(',', '.')
    stripped = re.sub('(\\.\\d\\d).*', '\\1', format_normalized)
    try:
        return Decimal(stripped)
    except decimal.InvalidOperation:
        return Decimal(0)


def dejunk(content):
    no_footers = re.sub('<hr/>\\s*<a[^>]+>[\\s\\S]+?(<img[^>]+><br/>\\s*)+.+?<br/>\\s*', '', content)
    no_trailer = re.sub('<b>Abschlussbetrag[\\S\\s]+', '', no_footers)
    no_random_code = re.sub('^.+?_T<br/>\\s*', '', no_trailer, flags=re.MULTILINE)
    
    return no_random_code


def resolve_and_validate_saldos(old_saldo, new_saldo, transactions):
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


def process_html(html_path):
    with open(html_path, 'r') as content_file:
        content = content_file.read()

    dejunked = dejunk(content)

    old_saldo, new_saldo = extract_saldos(content)

    # r = r'<b>(?:[\\s\\S](?!<b>))+'
    # r = r'\d\d.\d\d.\d\d\d\d<br/>\n<b>[^\n]+\n[^\n]+\n[^\n]+\n[^\n]+\n'
    # r = r'\d\d\.\d\d\.\d\d\d\d<br/>\n<b>[^\n]+\n[^\n]+\n\d\d.\d\d.\d\d\d\d[^\n]+\n'
    r = r'\d\d\.\d\d\.\d\d\d\d[^\n]*\n<b>[^\n]+\n[^\n]+\n\d\d.\d\d.\d\d\d\d[^\n]+(?:\n(?!\d\d\.\d\d\.\d\d\d\d)[^\n]+)?'
    table_entries = re.findall(r, dejunked)
    chunked_entries = [chunk_entry(e) for e in table_entries]
    plausible_entries = [e for e in chunked_entries if len(e) > 2]
    parsed_entries = [parse_entry(e) for e in plausible_entries]
    transactions = [e for e in parsed_entries if 'kind' in e and 'amount' in e and 'initiation' in e]
    return old_saldo, new_saldo, transactions


def convert_pdf(pdf_path, tmp_dir):
    pdf_filename = basename(pdf_path)
    no_ext = os.path.splitext(pdf_filename)[0]
    output_path = os.path.join(tmp_dir, no_ext + ".html")
    html_path = os.path.join(tmp_dir, no_ext + "s.html")

    # subprocess.run(executable="pdftohtml", args=[pdf_path], cwd=output_dir)
    subprocess.call(["pdftohtml", pdf_path, output_path], cwd=tmp_dir, stdout=sys.stderr)
    return html_path


def emit_csv(transactions):
    for entry in transactions:
        # print("###########")
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


def flatten(l):
    return [item for sublist in l for item in sublist]


def convert_all_pdfs():
    tmp_path = tempfile.mkdtemp(prefix="pdftohtml_")

    pdf_paths = [realpath(sys.argv[i]) for i in range(1, len(sys.argv))]
    html_paths = [convert_pdf(p, tmp_path) for p in pdf_paths]
    parsed_docs = [process_html(p) for p in html_paths]

    shutil.rmtree(tmp_path)

    final_transactions = flatten([resolve_and_validate_saldos(old, new, doc) for old, new, doc in parsed_docs])
    emit_csv(final_transactions)


convert_all_pdfs()
