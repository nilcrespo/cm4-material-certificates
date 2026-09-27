# CM4 Material Certificate Verification

Local web app that checks whether a supplier manufactured a part with the material CM4's BOM specifies,
by matching against material certificates (PDF). Built from three OpenSpec changes (all archived —
see `openspec/changes/archive/`): `material-certificate-verification` (the original engine),
`certificate-correspondence-matching` (the correspondence-document matching added after real EBRO data
disproved the original folder-name assumption), and `ocr-confidence-trust` (replacing the original
blanket "any OCR result needs review" rule with a confidence-and-confusability trust check) — see each
change's `proposal.md`/`design.md` for the reasoning behind the approach below.


> **About this repository.** Only the application code and tests are published. The real example data
> (supplier certificates, BOMs, correspondence documents under `examples/`) is confidential client data
> and is not included, nor are the OpenSpec design documents referenced below — tests that need the real
> data are skipped automatically when `examples/certificats/` is absent.

## Install and run

Requires Python 3.11+ and two external CLI tools: `poppler` (for `pdftotext`/`pdftoppm`) and `tesseract`.

```bash
brew install poppler tesseract   # macOS; already present on this machine
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --port 8001
```

Open `http://127.0.0.1:8001`. (Port 8000 is often already in use by another local tool — pick any free
port with `--port`.) The server only binds to localhost — nothing is exposed on the network, and
no uploaded file or extracted data is sent anywhere external.

Upload the BOM (`.xlsx` or `.csv` — comma or semicolon, UTF-8 or Windows-1252), a folder containing one
subfolder per supplier with the certificates (`.pdf`, `.docx`, `.doc`, or `.zip` files, as received), and
optionally a correspondence document (Excel/CSV with `ClientRef` and `Lote` columns) when the supplier
provides one — see `examples/certificats/` for the expected shape, including a real transcribed example
at `examples/certificats/EBRO/correspondencia_1170378.xlsx`. The BOM and certificates are required; the
correspondence document is optional.

Word certificates: text is read directly from a `.docx`; a `.docx` that is only a pasted scan is OCR'd
image by image, with the same confidence trust check as a scanned PDF. Legacy `.doc` uses macOS
`textutil`. The review panel shows Word certificates as a text+images preview with a download link.

### Language

The interface, suggested actions and Excel export are available in Catalan and Spanish (`CA | ES` in the
top bar, remembered per browser). All strings live in one catalog, `app/static/i18n.json`, used by both
the browser and the backend — add a key to both `ca` and `es` (a test checks they match).

### Run history

Every run is saved automatically as an Excel export into the history folder — by default
`~/Documents/CM4 verificacions/`, override with the `CM4_EXPORTS_DIR` environment variable — and re-saved
after each manual confirmation. The export includes a summary sheet (dates, input files, suppliers,
counts per status). The **Historial** tab lists every export in that folder (subfolders included, newest
first); older exports without a summary sheet — copied in by hand — are listed too, dated by file
modification time.

### Typeface

The UI uses Lab Grotesque; drop the font files into `app/static/fonts/` (see the README there). Until
then it falls back to the system sans-serif.

## Running the tests

```bash
source .venv/bin/activate
pytest
```

Tests run real `pdftotext`/`tesseract` subprocess calls against the files in `examples/certificats/` (no
mocking of extraction), so they take a bit longer than typical unit tests but validate the actual OCR
pipeline, not just the code around it.

## Known gaps (v1)

These are deliberate scope boundaries, not bugs — most are already called out as open questions in
`design.md`:

- **EBRO's direct-reference folder convention was a wrong guess — resolved by the correspondence document
  instead.** The zip folder names (`11703780001`, ...) turned out to be `Albarán + Pos` (an EBRO shipment
  line id), not a CM4 reference — confirmed against a real delivery email
  (`examples/certificats/CM4_spec_Certificats.pdf`). The real link is a separate correspondence document
  (`ClientRef → Lote`, where `Lote` is the certificate's filename stem), now implemented as the
  top-priority matching strategy (`app/correspondence.py`, wired into `app/matching.py`). It's scoped to
  one delivery at a time — a BOM reference not covered by a given correspondence document simply falls
  through to the older strategies, and is flagged with the distinct `not_in_correspondence` reason if
  nothing else resolves it either (rather than the generic `no_match_found`), since a missing document
  row is a data gap to fix at the source, not a matching failure to debug. A real reference suffix
  variant (`M009-L206-1` in the correspondence document vs. the BOM's plain `M009-L206`) is reconciled
  automatically by `matching.find_correspondence_key` (exact match first, then a trailing `-<digits>`
  stripped from the document's side) — a genuinely different reference never matches this way, since the
  stripped form must equal the BOM reference exactly.
- **The material-standard-equivalence table (`app/data/equivalence.yaml`) only covers the grades seen in
  the example certificates.** Any material not in the table will not be recognized even when a correct
  certificate is present — it will land in `needs_review`/`no_match_found` rather than `ok`. This is safe
  (never a false pass) but means the table needs to grow as new materials/suppliers are seen. Extending it
  is a config change, not a code change.
- **Lot/heat-number matching has no input path from the app yet.** `app/matching.py`'s lot-number
  strategy is implemented and tested (see `tests/test_matching.py`), but nothing in the web app currently
  parses an albarà/PO into a `lot_map` for it to use — that would need a parser for whatever a given
  supplier's albarà actually looks like, which no single generic format covers. Until that's built,
  lot-number matching is available in the engine but effectively unused end-to-end.
- **OCR field extraction is a small set of hand-written regexes** (`app/extraction.py`), not a general
  document-layout parser. It works for the grade/standard patterns seen in the three example suppliers;
  a genuinely new certificate layout may extract nothing useful. When that happens, the part correctly
  lands in `needs_review` rather than being silently wrong — see `test_end_to_end.py`'s IAMCUT case, where
  real OCR runs successfully but the extracted `EN 573` standard isn't in the equivalence table, so the
  part is held for review rather than guessed at.
- **An OCR-derived result no longer always needs review — it needs to pass a trust check first**
  (`app/equivalence.py`'s `is_reading_trustworthy`): the extracted grade must be read at or above
  `OCR_TRUST_MIN_CONFIDENCE` (currently `80.0`, Tesseract's own 0–100 word confidence) AND not sit exactly
  one common OCR-confusable character swap (e.g. `6`/`8`, `2`/`Z`, `5`/`S`) away from a *different* real
  material already in the equivalence table. Both conditions are required — confidence alone can't catch a
  confident misread of one valid grade as another, and confusability alone can't catch a low-quality read
  that just happens not to collide with a different known grade. Tune `OCR_TRUST_MIN_CONFIDENCE` in
  `app/equivalence.py` if real usage shows it's too strict or too lax; it was chosen with a wide margin
  against two real certificates (a clean read scored ~86.6, a genuinely noisy-but-correct one scored
  ~15.6 — see `tests/test_extraction.py`'s ground-truth confidence check for how to re-derive this against
  new data).
- **`VerificationRecord.committed_material` (the material a supplier's albarán/delivery note
  commits to) has no producer yet.** The field is wired through the model and the Excel export, but
  every example albarán checked so far (`examples/certificats/RIERA/ALB_202307573.pdf`) is a pure
  packing list — parts, quantities, prices — with no material field at all. Populating this needs a
  real albarán that states a material, which no supplier example currently provides.
- **A run can't be reopened for review after a server restart.** Its export (with every decision) is
  saved in the history folder and shown in **Historial**, but the live run — needed to open certificates
  and confirm rows — lives in memory (`app/main.py`'s `_runs`) and is lost on restart. Confirmed
  equivalence pairs (`app/data/confirmed_pairs.json`) persist to disk.

## OCR accuracy fixes (from a manual review of all 15 real LASER references)

- **The grade/standard regexes were too strict about the separator right after a prefix letter.**
  Real certificates OCR to (or are genuinely printed as) `S-235JR`, `EN- 10025`, and `EN AW 5754`
  (space, not the usual hyphen) - none of these matched before, so the part silently showed no
  extracted material at all even though the grade was clearly present in the text (real cases:
  `M009-L103`, `M009-L802`, `M009-L811`). `app/extraction.py`'s patterns now tolerate a stray
  hyphen/space there and `_clean_match` normalizes the result back to the canonical form
  (`S235JR`, `EN AW-5754`) equivalence.yaml and the BOM use, so classification isn't affected by
  the extra tolerance - only recognition is.
- **A non-3.1 certificate now has its own review reason** (`ReviewReason.CERTIFICATE_TYPE`),
  taking priority over the generic OCR-source one when the material itself matched cleanly - real
  case: EBRO's own certificates are consistently EN 10204 2.2, and the previous behaviour blamed
  "OCR uncertainty" even when the grade was read correctly and confidently. The reading is still
  shown (`extracted_material` is never blanked for this reason); only the displayed reason and
  `suggested_action` change to explain that a human needs to accept a 2.2 declaration specifically,
  not that the OCR was unclear.
- **A correspondence-document row with no matching BOM reference at all** (real case:
  `M009-L114 -> ZA33404` in `correspondencia_1170378.xlsx`, which has no `M009-L114` in the LASER
  BOM) is now surfaced as its own list (`orphan_correspondence_entries` in the API response, a
  second sheet in the Excel export, a callout in the UI) instead of being silently dropped -
  there's no BOM row to attach it to, so it can't appear as a `VerificationRecord`.
- **Confirming a decision is no longer limited to `needs_review` rows** - `POST
  /api/runs/{id}/confirm` now accepts any row that has a certificate linked (the actual condition
  is "is there something to look at", not "is the status needs_review"), so an `ok` or `mismatch`
  row can be overridden too after looking at the certificate PDF in the review drawer. A row with
  no linked certificate still can't be confirmed - there's nothing to confirm it against.
- **A low-confidence OCR reading intentionally still forces `needs_review`, even when it happens
  to compute as a clean `mismatch`** (real case: `M009-L111`, where the actual scan does read
  `EN AW-5754` against a specified `EN AW 6063` - a genuine mismatch, confirmed on visual
  inspection - but the OCR confidence for that specific reading is ~15.6, well under the trust
  threshold). This isn't a bug: it's the same safety margin as the OK side of the trust check
  (`app/equivalence.py`'s `is_reading_trustworthy`) applied symmetrically, and the intended
  resolution is exactly the review UI - open the row, look at the embedded PDF, and use "Marcar
  discrepància" to record the confirmed outcome, rather than the code auto-deciding from a reading
  it can't itself vouch for.
