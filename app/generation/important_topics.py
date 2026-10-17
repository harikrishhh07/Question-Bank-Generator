"""Important Topics generation — analyze question bank, rank topics by frequency/marks."""
from __future__ import annotations

import re
import html as _html
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from ..config import settings
from ..models import (
    Collection,
    Document,
    ImportantTopicsGeneration,
    Question,
    SyllabusUnit,
)
from ..storage import storage


# ---------------------------------------------------------------------------
# Topic extraction -- keyword-based (fast, no LLM per question)
# ---------------------------------------------------------------------------

# Common stop words to suppress
_STOP_WORDS = {
    "the", "a", "an", "of", "in", "to", "and", "or", "with", "for", "is",
    "are", "that", "this", "what", "how", "why", "when", "which", "find",
    "show", "prove", "verify", "evaluate", "determine", "define", "state",
    "explain", "derive", "compute", "calculate", "solve", "use", "using",
    "given", "following", "each", "all", "any", "let", "if", "then",
    "from", "by", "on", "at", "be", "its", "it", "as", "between",
}

# Strip leading numbering noise
_NOISE_RE = re.compile(r"^\s*(\(?[a-z]{1,2}[0-9]?\)?\.?|[ivxIVX]+\.|\d+\.)\s*", re.IGNORECASE)


def _extract_topics_from_text(text: str) -> list[str]:
    """Extract meaningful keyword phrases from question text without LLM calls."""
    if not text or len(text.strip()) < 10:
        return []
    clean = text.strip()
    return [clean]  # return the full original question text


def _normalize_topic(t: str) -> str:
    """Lowercase, strip punctuation for grouping."""
    return re.sub(r"[^a-z0-9 ]", "", t.lower()).strip()


# ---------------------------------------------------------------------------
# Syllabus-based topic mapping (if syllabus units exist)
# ---------------------------------------------------------------------------

def _map_to_syllabus_unit(question_text: str, units: list[SyllabusUnit]) -> int | None:
    """Return the unit id whose topics best match this question, or None."""
    if not units or not question_text:
        return None
    q_lower = question_text.lower()
    best_unit = None
    best_score = 0
    for unit in units:
        score = 0
        for topic in (unit.topics or []):
            words = topic.lower().split()
            for word in words:
                if len(word) > 4 and word in q_lower:
                    score += 1
        if score > best_score:
            best_score = score
            best_unit = unit.id
    return best_unit if best_score > 0 else None


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------

def analyze_collection(db: Session, collection_id: int) -> list[dict]:
    """Return topic ranking from questions, grouped by unit (or ungrouped if no syllabus)."""
    questions = (
        db.query(Question)
        .join(Document, Question.document_id == Document.id)
        .filter(
            Document.collection_id == collection_id,
            Question.status.in_(["approved", "review", "pending"]),
        )
        .all()
    )
    if not questions:
        return []

    units = (
        db.query(SyllabusUnit)
        .filter(SyllabusUnit.collection_id == collection_id)
        .order_by(SyllabusUnit.unit_number)
        .all()
    )

    # Group questions by unit
    unit_questions: dict[int | str, list[Question]] = defaultdict(list)
    for q in questions:
        uid = _map_to_syllabus_unit(q.text or "", units)
        bucket = uid if uid is not None else "General"
        unit_questions[bucket].append(q)

    # Build unit map
    unit_map = {u.id: u for u in units}

    result = []
    for bucket_key, qs in unit_questions.items():
        # Aggregate topics
        topic_data: dict[str, dict] = {}
        for q in qs:
            raw_topics = _extract_topics_from_text(q.text or "")
            for raw in raw_topics:
                norm = _normalize_topic(raw)
                if not norm:
                    continue
                if norm not in topic_data:
                    topic_data[norm] = {
                        "topic": raw,
                        "count": 0,
                        "total_marks": 0,
                        "questions": [],
                    }
                topic_data[norm]["count"] += 1
                topic_data[norm]["total_marks"] += q.marks or 0
                topic_data[norm]["questions"].append({
                    "id": q.id,
                    "text": (q.text or "")[:200],
                    "marks": q.marks,
                    "part": q.part,
                    "number": q.number,
                })

        # Sort topics by count DESC, then marks DESC
        ranked = sorted(
            topic_data.values(),
            key=lambda x: (x["count"], x["total_marks"]),
            reverse=True,
        )

        if isinstance(bucket_key, int) and bucket_key in unit_map:
            unit = unit_map[bucket_key]
            unit_info = {"unit": unit.unit_number, "title": unit.title, "unit_id": unit.id}
        else:
            unit_info = {"unit": 0, "title": "General / Unclassified", "unit_id": None}

        result.append({
            **unit_info,
            "question_count": len(qs),
            "topics": ranked[:30],  # top 30 per unit
        })

    # Sort units by unit number
    result.sort(key=lambda x: x["unit"])
    return result


# ---------------------------------------------------------------------------
# PDF generation
# ---------------------------------------------------------------------------

def _render_html(rankings: list[dict], meta: dict) -> str:
    """Render the important topics report as HTML."""
    units_html = ""
    for unit in rankings:
        unit_num = unit["unit"]
        unit_title = unit["title"]
        q_count = unit["question_count"]
        topics = unit["topics"]

        rows = ""
        for rank, t in enumerate(topics, 1):
            count = t["count"]
            marks = t["total_marks"]
            importance = "🔴 High" if count >= 3 else ("🟡 Medium" if count == 2 else "🟢 Low")
            rows += f"""<tr>
              <td class="rank">{rank}</td>
              <td class="topic">{_html.escape(t['topic']).replace("\n", "<br>")}</td>
              <td class="count">{count}</td>
              <td class="marks">{marks}</td>
              <td class="imp">{importance}</td>
            </tr>"""

        if not rows:
            rows = '<tr><td colspan="5" class="empty">No topics extracted for this unit.</td></tr>'

        heading = f"Unit {unit_num}: {unit_title}" if unit_num else unit_title
        units_html += f"""
        <div class="unit-section">
          <div class="unit-header">
            <h2>{heading}</h2>
            <span class="unit-meta">{q_count} questions analyzed</span>
          </div>
          <table>
            <thead>
              <tr>
                <th>#</th><th>Topic / Concept</th><th>Frequency</th>
                <th>Total Marks</th><th>Importance</th>
              </tr>
            </thead>
            <tbody>{rows}</tbody>
          </table>
        </div>"""

    css = """
    @page { size: A4; margin: 18mm 16mm; }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { font-family: 'Segoe UI', Arial, sans-serif; font-size: 10.5pt;
           color: #111; background: #fff; }
    .report-header { text-align: center; border-bottom: 2px solid #fe6e00;
                     padding-bottom: 10px; margin-bottom: 18px; }
    .report-header h1 { font-size: 14pt; font-weight: 800; color: #fe6e00; }
    .report-header .sub { font-size: 11pt; margin-top: 4px; }
    .report-header .meta { font-size: 9.5pt; color: #555; margin-top: 6px; }
    .disclaimer { background: #fff8f0; border-left: 3px solid #fe6e00;
                  padding: 8px 12px; margin-bottom: 18px; font-size: 9pt;
                  color: #6b4200; }
    .unit-section { margin-bottom: 24px; page-break-inside: avoid; }
    .unit-header { display: flex; justify-content: space-between; align-items: baseline;
                   background: #f4f4f4; padding: 6px 10px; border-left: 4px solid #fe6e00;
                   margin-bottom: 8px; }
    .unit-header h2 { font-size: 11pt; font-weight: 700; }
    .unit-meta { font-size: 9pt; color: #666; }
    table { width: 100%; border-collapse: collapse; font-size: 9.5pt; }
    th { background: #333; color: #fff; padding: 6px 10px; text-align: left; font-weight: 600; }
    td { padding: 6px 10px; border-bottom: 1px solid #e0e0e0; word-break: break-word; overflow-wrap: anywhere; vertical-align: top; }
    tr:nth-child(even) { background: #fafafa; }
    .rank { width: 32px; text-align: center; color: #888; }
    .topic { white-space: pre-wrap; line-height: 1.5; word-break: break-all; overflow-wrap: anywhere; width: auto; min-width: 0; font-size: 9pt; }
    .count { width: 72px; text-align: center; font-weight: 700; }
    .marks { width: 80px; text-align: center; }
    .imp { width: 90px; }
    .empty { text-align: center; color: #999; padding: 12px; }
    """

    subject_info = (
        f"{meta.get('subject_code', '')} — {meta.get('subject_name', '')}"
        if meta.get("subject_code") else meta.get("subject_name", "Important Topics Report")
    )
    compiled = datetime.now(timezone.utc).strftime("%d %B %Y")

    # Embed logo as base64 for the report header
    import base64 as _b64
    _logo_path = Path(__file__).parent.parent.parent / "data" / "files" / "assets" / "studique-logo.png"
    _logo_tag = ""
    if _logo_path.exists():
        _logo_b64 = _b64.b64encode(_logo_path.read_bytes()).decode()
        _logo_tag = f'<img src="data:image/png;base64,{_logo_b64}" alt="Studique" style="width:36px;height:36px;border-radius:7px;vertical-align:middle;margin-right:10px;">'

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<link rel="stylesheet" href="katex.min.css">
<script src="katex.min.js"></script>
<script src="auto-render.min.js"></script>
<style>{css}</style>
</head>
<body>
<div class="report-header">
  <div style="display:flex;align-items:center;justify-content:center;gap:10px;margin-bottom:6px">
    {_logo_tag}<span style="font-size:14pt;font-weight:800;letter-spacing:0.05em;color:#fe6e00">STUDIQUE</span>
  </div>
  <div class="sub">Important Topics Report</div>
  <div class="sub">{subject_info}</div>
  <div class="meta">Compiled on {compiled} · Based on {meta.get('total_questions', 0)} questions</div>
</div>
<div class="disclaimer">
  ⚠️ This report is based on historical question frequency analysis only. Topic importance reflects
  past examination patterns and is NOT a guarantee that any topic will appear in future examinations.
  Frequency ≥ 3 = High, 2 = Medium, 1 = Low importance.
</div>
{units_html}
</body>
</html>"""


def _generate_pdf(html: str, output_path: Path) -> None:
    from pathlib import Path as _Path
    from playwright.sync_api import sync_playwright

    katex_dir = _Path(__file__).parent / "templates" / "katex"
    tmp_html = katex_dir / "_topics_render.html"
    tmp_html.write_text(html, encoding="utf-8")
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(args=["--single-process", "--no-sandbox"])
            page = browser.new_page()
            page.goto(tmp_html.resolve().as_uri(), wait_until="networkidle")
            page.evaluate("""() => {
                if (typeof renderMathInElement === 'function') {
                    renderMathInElement(document.body, {
                        delimiters: [
                            {left: '\\\\(', right: '\\\\)', display: false},
                            {left: '\\\\[', right: '\\\\]', display: true},
                            {left: '$$', right: '$$', display: true},
                            {left: '$', right: '$', display: false}
                        ],
                        throwOnError: false,
                        errorColor: '#cc0000'
                    });
                }
            }""")
            page.wait_for_timeout(800)
            page.pdf(
                path=str(output_path),
                format="A4",
                print_background=True,
                prefer_css_page_size=True,
            )
            browser.close()
    finally:
        tmp_html.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def generate_important_topics(db: Session, collection_id: int, config: dict) -> dict:
    """Analyze collection and generate important-topics PDF.

    config keys:
      - name         : report name
      - subject_name : str
      - subject_code : str
    """
    collection = db.query(Collection).filter(Collection.id == collection_id).first()
    if not collection:
        raise ValueError(f"Collection {collection_id} not found")

    unit_rankings = analyze_collection(db, collection_id)
    total_questions = sum(u["question_count"] for u in unit_rankings)

    meta = {
        "subject_name": config.get("subject_name", ""),
        "subject_code": config.get("subject_code", ""),
        "total_questions": total_questions,
    }

    html = _render_html(unit_rankings, meta)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    name = config.get("name") or "Important_Topics"
    out_key = f"banks/topics_{collection_id}_{stamp}.pdf"
    out_path = storage.path(out_key)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    _generate_pdf(html, out_path)

    gen = ImportantTopicsGeneration(
        collection_id=collection_id,
        name=name,
        output_key=out_key,
        unit_rankings=unit_rankings,
        total_questions_analyzed=total_questions,
    )
    db.add(gen)
    db.commit()

    return {
        "generation_id": gen.id,
        "output_key": out_key,
        "name": name,
        "total_questions": total_questions,
        "units": len(unit_rankings),
    }
