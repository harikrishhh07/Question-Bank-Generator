import json
import re
import time

from sqlalchemy.orm import Session

from .config import settings
from .llm import llm
from .models import Answer, AnswerGeneration, Question

# Heuristic: a question is "math-heavy" if its text contains LaTeX commands or math symbols.
_MATH_SYMBOL_RE = re.compile(
    r"\\[a-zA-Z]+|∫|∑|√|π|∞|∇|∂|×|±|≤|≥|→|∈|Σ|Π|\[|_\{|\^\{"
)


def _is_math_question(q: Question) -> bool:
    text = (q.text or "")
    for sub in (q.subs or []):
        text += " " + (sub.get("text") or "")
    for opt in (q.options or []):
        text += " " + str(opt)
    return bool(_MATH_SYMBOL_RE.search(text))


def _call_with_retry(prompt: str, math: bool, attempts: int = 4) -> str:
    """Call the LLM with retry/backoff. Uses the strong model for math questions."""
    model = settings.openai_strong_model if math else settings.openai_model
    last_exc = None
    for attempt in range(attempts):
        try:
            response = llm._client.responses.create(
                model=model,
                input=[
                    {"role": "system", "content": [{"type": "input_text", "text": _build_system_prompt()}]},
                    {"role": "user", "content": [{"type": "input_text", "text": prompt}]},
                ],
                text={"format": {"type": "json_object"}},
            )
            return response.output_text
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if attempt < attempts - 1:
                time.sleep(3 * (attempt + 1))
    raise last_exc


def _build_question_prompt(q: Question) -> str:
    parts = []
    if q.text:
        parts.append(q.text)
    if q.options:
        parts.append("\nOptions:\n" + "\n".join(q.options))
    if q.subs:
        sub_lines = []
        for s in q.subs:
            or_marker = " (OR alternative)" if s.get("is_or_alternative") else ""
            sub_lines.append(f"{s.get('label') or ''} {s.get('text', '')}{or_marker}")
        parts.append("\nSub-questions:\n" + "\n".join(sub_lines))
    return "\n".join(parts)


def _build_system_prompt() -> str:
    return """You are a mathematics tutor writing model answers exactly as a top student writes them in an exam answer sheet.

Write concise, correct, step-by-step solutions. The student has already seen the question — do NOT restate it, do NOT explain "how to approach" it. Just show the working.

Return ONLY valid JSON with exactly this schema:
{
  "steps": ["line 1 of working", "line 2 of working", ...],
  "final_answer": "the final boxed result",
  "correct_option": "A"
}

STYLE (critical):
- Write like a student solving on paper: short lines of mathematics, minimal words.
- NEVER use AI-style filler: "we can see that", "which states that", "Thus, we have", "Therefore, the final result is", "After computations", "we need to", "we can find", "this yields", "note that".
- Do NOT restate the question. Start directly with the working.
- Each step is one compact line of math, e.g.:
  (a) \\( \\frac{\\partial}{\\partial y}(4x+cy+2z) - \\frac{\\partial}{\\partial z}(bx-3y-z) = c-(-1) = c+1 \\)
- Label sub-parts (a), (b), (c) inline at the start of the relevant step. For OR alternatives, solve (a) fully and add one short line for (b).
- For MCQ: give the correct option letter and 1-3 lines of working that justifies it.
- Keep steps between 1 and 6 lines.

MATH:
- Write all math in LaTeX delimited by \\( \\) (inline). 
- Delimiters must wrap the WHOLE mathematical expression. NEVER place \\( or \\) inside a \frac{...}{...} or between terms of one expression — the entire expression belongs in one \\( ... \\) group.
- Use standard notation: \\frac, \\partial, \\int, \\sum, \\sqrt, \\lim, \\nabla, \\times, \\cdot.
- For a vector write it as a linear combination (e.g. \\(x\\mathbf{i}+y\\mathbf{j}+z\\mathbf{k}\\)), not a matrix.

JSON:
- Escape every backslash as a double backslash so the JSON is valid.
- final_answer: the final result, in math.
- correct_option: only for MCQ questions (A/B/C/D); otherwise omit."""


def repair_latex_backslashes(text: str) -> str:
    """Repair LaTeX corrupted by JSON parsing.

    The LLM often emits unescaped backslashes in JSON (e.g. `\\boldsymbol`,
    `\\text`, `\\begin`). json.loads then interprets `\\b` as backspace,
    `\\t` as tab, `\\n` as newline, etc. This restores them:
      \x08 -> \b   \x09 -> \t   \x0c -> \f   \x0d -> \r   \x0a -> \n (when a command)
    """
    text = (
        text.replace("\x08", "\\b")
        .replace("\x09", "\\t")
        .replace("\x0c", "\\f")
        .replace("\x0d", "\\r")
    )
    # newline -> \n only when it starts a LaTeX command (followed by a letter)
    text = re.sub(r"\x0a(?=[a-zA-Z])", r"\\n", text)
    # collapse remaining newlines that sit inside math delimiters (KaTeX can't render them)
    text = _collapse_newlines_in_math(text)
    # normalize delimiters with 2+ backslashes down to a single one: \\( -> \(, \\\\[ -> \[
    text = re.sub(r"\\{2,}([\[\]()])", r"\\\1", text)
    return text


def finalize_answer_text(text: str) -> str:
    r"""Make answer LaTeX render cleanly and read like exam working."""
    from .pipeline.postprocess import _repair_delims

    text = repair_latex_backslashes(text or "")
    text = _remove_spurious_delimiters(text)
    text = _repair_delims(text)
    # stray \. (broken dot-accent) breaks KaTeX -> replace with a plain period
    text = re.sub(r"\\\.(?![a-zA-Z{])", ".", text)
    # wrap a math line that has no delimiters yet so KaTeX renders it
    if "\\(" not in text and "\\[" not in text and re.search(r"\\[a-zA-Z]+|[\^_]=", text):
        text = "\\(" + text.strip() + "\\)"
    return text


def _remove_spurious_delimiters(text: str) -> str:
    r"""Drop \( \) \[ \] delimiters that appear inside {…} brace groups
    (e.g. inside \frac{…}{…}), where the model misplaced them mid-expression."""
    out: list[str] = []
    i, n = 0, len(text)
    depth = 0
    while i < n:
        c = text[i]
        if c == "{":
            depth += 1
            out.append(c)
            i += 1
        elif c == "}":
            depth = max(0, depth - 1)
            out.append(c)
            i += 1
        elif text.startswith("\\(", i) or text.startswith("\\[", i) or text.startswith("\\)", i) or text.startswith("\\]", i):
            if depth > 0:
                i += 2  # spurious delimiter inside braces -> drop it
            else:
                out.append(text[i : i + 2])
                i += 2
        else:
            out.append(c)
            i += 1
    return "".join(out)


def _collapse_newlines_in_math(text: str) -> str:
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        if text.startswith("\\(", i) or text.startswith("\\[", i):
            close = "\\)" if text.startswith("\\(", i) else "\\]"
            j = text.find(close, i + 2)
            if j == -1:
                j = n
            seg = text[i + 2 : j].replace("\n", " ").replace("\r", " ")
            out.append(text[i : i + 2] + seg + (close if j < n else ""))
            i = j + 2 if j < n else n
        else:
            out.append(text[i])
            i += 1
    return "".join(out)


def _parse_answer_response(text: str) -> dict:
    """Parse LLM JSON output, tolerating code fences and stray text."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.S)
        if not m:
            return {"steps": [text], "final_answer": text, "correct_option": None, "_parse_failed": True}
        try:
            data = json.loads(m.group(0))
        except json.JSONDecodeError:
            return {"steps": [text], "final_answer": text, "correct_option": None, "_parse_failed": True}

    steps = data.get("steps") or []
    if isinstance(steps, str):
        steps = [steps]
    final_answer = data.get("final_answer") or steps[-1] if steps else ""
    return {
        "steps": [finalize_answer_text(str(s)) for s in steps],
        "final_answer": finalize_answer_text(str(final_answer)),
        "correct_option": data.get("correct_option") or None,
    }


def generate_answer_for_question(q: Question) -> dict:
    """Solve one question with the LLM and return the answer content dict."""
    prompt = _build_question_prompt(q)
    math = _is_math_question(q)
    response = _call_with_retry(prompt, math)
    return _parse_answer_response(response)


def generate_answers_for_collection(db: Session, collection_id: int) -> dict:
    """Generate answers for all questions in a collection. Returns progress dict."""
    from .models import Document

    gen = (
        db.query(AnswerGeneration).filter(AnswerGeneration.collection_id == collection_id).first()
    )
    if gen is None:
        gen = AnswerGeneration(collection_id=collection_id)
        db.add(gen)
    gen.status = "running"
    gen.completed = 0
    db.commit()

    questions = (
        db.query(Question)
        .join(Document, Question.document_id == Document.id)
        .filter(Document.collection_id == collection_id)
        .all()
    )
    gen.total = len(questions)
    db.commit()

    errors = 0
    for i, q in enumerate(questions):
        # keep existing successful answers
        existing = db.query(Answer).filter(Answer.question_id == q.id).first()
        if existing and existing.status == "generated":
            gen.completed = i + 1
            db.commit()
            continue
        try:
            content = generate_answer_for_question(q)
            if existing is None:
                existing = Answer(question_id=q.id)
                db.add(existing)
            existing.content = content
            existing.status = "error" if content.get("_parse_failed") else "generated"
            existing.error = None
            existing.model = settings.openai_model
            errors += 1 if content.get("_parse_failed") else 0
        except Exception as exc:
            if existing is None:
                existing = Answer(question_id=q.id)
                db.add(existing)
            existing.status = "error"
            existing.error = str(exc)
            errors += 1
        gen.completed = i + 1
        db.commit()
        time.sleep(0.4)  # gentle pacing to avoid rate limits

    gen.status = "done"
    gen.error = f"{errors} failed" if errors else None
    db.commit()
    return {"status": "done", "total": gen.total, "completed": gen.completed, "errors": errors}
