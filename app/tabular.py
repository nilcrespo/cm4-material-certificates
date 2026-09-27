import csv
import io

# Excel writes CSV as UTF-8 (often with a BOM) or, in Spanish/Catalan locales, Windows-1252 -
# and in those locales with ";" as the delimiter, since "," is the decimal separator.
_ENCODINGS = ("utf-8-sig", "cp1252")
_DELIMITERS = ",;\t"


def decode_csv(content: bytes) -> str:
    for encoding in _ENCODINGS:
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    return content.decode("latin-1")  # never fails; last resort


def read_csv_rows(content: bytes) -> list[list[str]]:
    """Rows of a CSV file as lists of strings, with encoding and delimiter detected."""
    text = decode_csv(content)
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=_DELIMITERS)
        delimiter = dialect.delimiter
    except csv.Error:
        first_line = sample.splitlines()[0] if sample else ""
        delimiter = ";" if first_line.count(";") > first_line.count(",") else ","
    return [row for row in csv.reader(io.StringIO(text), delimiter=delimiter)]
