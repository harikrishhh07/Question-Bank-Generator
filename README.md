# Studique QBGen

Automated system that processes Previous Year Question (PYQ) PDFs and generates professional, branded Studique Question Banks.

## What it does

```
PYQ PDFs ──▶ ingestion ──▶ OCR / text layer ──▶ VLM document understanding
       ──▶ question extraction ──▶ sub-question coalescing (a/b/c, OR)
       ──▶ media (diagram/table) detection & association
       ──▶ validation & confidence scoring ──▶ review UI
       ──▶ branded Question Bank PDF (watermark + KaTeX math)
```

Key capabilities:

- **Handles both scanned and born-digital PDFs.** Scanned pages are OCR'd with Tesseract; born-digital pages use the embedded text layer.
- **Vision-language understanding.** Each page is analyzed by GPT-4o-mini which extracts the document header (subject code/name, exam, semester, max marks), part structure, questions, sub-questions, options, marks, and media regions with bounding boxes.
- **Question normalization.** Parts are corrected from section headers, marks overridden from part defaults (e.g. `20 x 1` → 1 mark each, ignoring the BL/CO/PO table), and split `21.a` / `21.b` (OR) alternatives are coalesced into sub-questions.
- **Marks weightage** is computed and shown per section and on the cover.
- **Visual preservation.** Media regions are cropped at 300 DPI, stored as high-res originals plus thumbnails, and associated with questions (shared diagrams reference one media object).
- **Confidence + review.** Every question carries per-dimension confidence and explainable flags. The review UI lets you edit text, marks, sub-questions, attach/detach media, and approve/reject.
- **Regeneration.** The bank is generated from structured data, so any correction or template change only requires a re-render.

## Quick start

1. Install requirements:

   ```
   pip install -r requirements.txt
   python -m playwright install chromium
   ```

2. Copy your OpenAI API key into `.env` (the file is already created and git-ignored):

   ```
   OPENAI_API_KEY=sk-...
   ```

3. (Optional) Install Tesseract OCR for scanned PDFs — already installed at
   `C:\Program Files\Tesseract-OCR`. Override the path in `.env` if needed:

   ```
   TESSERACT_PATH=C:\Program Files\Tesseract-OCR\tesseract.exe
   ```

4. Start the app:

   ```
   python scripts/serve.py
   ```

5. Open http://127.0.0.1:8000

## Using the app

1. **Collections** tab — create a collection (one per subject / batch), upload one or more PYQ PDFs. Processing starts automatically in a background worker.
2. **Review** tab — select a document, inspect extracted questions, edit text/marks/sub-questions, toggle media attachments, and approve/reject. The source page renders alongside for verification.
3. **Generate** tab — choose a collection, configure the bank (title, subject override, course, semester, grouping by part/year/type, notes), and click **Generate Question Bank**. The PDF appears in the Generated Banks list.

## Configuration (.env)

| Variable | Default | Purpose |
|---|---|---|
| `OPENAI_API_KEY` | — | API key for the VLM tier |
| `OPENAI_MODEL` | `gpt-4o-mini` | Default document-understanding model |
| `OPENAI_STRONG_MODEL` | `gpt-4o-mini` | Model used to retry pages with empty extraction |
| `DATABASE_URL` | `sqlite:///./data/studique.db` | SQLite by default; any SQLAlchemy URL works (e.g. Postgres) |
| `TESSERACT_PATH` | `C:\Program Files\Tesseract-OCR\tesseract.exe` | OCR binary |
| `WATERMARK_IMAGE` | `assets/studique-logo.png` | Watermark image path |
| `WATERMARK_OPACITY` | `0.08` | Watermark opacity (0–1) |
| `WATERMARK_SCALE` | `0.4` | Watermark scale (0–1) |

## Project layout

```
app/
  config.py            # settings (.env)
  db.py / models.py    # SQLAlchemy schema
  storage.py           # local file storage (swap for S3 later)
  llm.py               # VLM wrapper (OpenAI)
  pipeline/
    rasterize.py       # PDF -> page renders + text layer
    ocr.py             # Tesseract OCR with adaptive preprocessing
    doc_understand.py  # VLM page understanding
    postprocess.py     # part/marks fixing, sub-question coalescing
    associate.py       # media <-> question association
    validate.py        # confidence + explainable flags
    pipeline.py        # document orchestrator
    job_runner.py      # background worker (thread)
  generation/
    context.py         # query builder + template context
    document.py        # HTML -> PDF (Playwright + KaTeX + watermark)
    templates/standard # Jinja2 template + CSS
  web/
    main.py            # FastAPI app + API routes
    static/            # review UI (HTML/JS/CSS)
scripts/
  serve.py             # start the app
```

## Notes

- Database is SQLite by default (zero infra). Set `DATABASE_URL` to a Postgres URL to switch.
- All files (page renders, media crops, source PDFs, generated banks) live under `data/files/`.
- Processing is tiered: OCR/text-layer first, VLM on every page for structure (GPT-4o-mini is ~$0.0002/page), and the strong model only on pages where extraction came back incomplete.
- The watermark is a `position: fixed` layer repeated on every printed page by Chromium, always behind content, so it never obscures diagrams, tables, or equations.
