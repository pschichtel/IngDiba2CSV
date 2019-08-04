# IngDiba2CSV

A simple python script that takes a bunch of bank statement PDFs from ing-diba.de and converts them into a single CSV file. It uses poppler's pdftohtml command as convert the PDFs to HTML files as an intermediate step and then extracts the details from there.
Each converted file is checked against the old and new saldo to verify that all entries have been correctly read.
