from __future__ import annotations

import json
import re
import time

from sqlalchemy.orm import Session

from .config import settings
from .llm import llm
from .models import Answer, AnswerGeneration, Document, Question

# ── Heuristic: is this question math-heavy? ──────────────────────────────────
_MATH_SYMBOL_RE = re.compile(
    r"\\[a-zA-Z]+|∫|∑|√|π|∞|∇|∂|×|±|≤|≥|→|∈|Σ|Π|\^{|\_{|\\frac|\\int"
)


def _is_math_question(q: Question) -> bool:
    text = q.text or ""
    for sub in (q.subs or []):
        text += " " + (sub.get("text") or "")
    for opt in (q.options or []):
        text += " " + str(opt)
    return bool(_MATH_SYMBOL_RE.search(text))


# ── System / question prompts ─────────────────────────────────────────────────
def _system_prompt() -> str:
    return """You are writing model answers for an engineering university exam (Anna University / VTU style).

Write concise, correct step-by-step solutions. DO NOT restate the question.

Return ONLY valid JSON (no markdown fences):
{
  "steps": ["step 1", "step 2", ...],
  "final_answer": "the final result",
  "correct_option": "A"
}

RULES:
- steps: array of short working lines. 3-8 lines for long questions, 1-3 for short ones.
- final_answer: the boxed result in LaTeX if it is math.
- correct_option: only for MCQ (A/B/C/D). Omit for descriptive questions.
- NO filler phrases: never write "we can see that", "thus we have", "therefore", "note that", "it is given that".
- Start directly with the mathematics or the first logical step.
- Label sub-parts (a), (b) at the start of the step where they begin.
- For OR alternatives: solve the first alternative fully, then add one line for the second.

MATH in JSON strings:
- Use LaTeX delimiters: \\( ... \\) for inline, \\[ ... \\] for display equations.
- Inside JSON, every backslash must be doubled: \\frac becomes \\\\frac.
- Example step: "\\\\( x = \\\\frac{-b \\\\pm \\\\sqrt{b^2 - 4ac}}{2a} \\\\)"
"""


def _question_prompt(q: Question) -> str:
    parts = []
    if q.text:
        parts.append(q.text)
    if q.subs:
        for s in q.subs:
            label = s.get("label") or ""
            text = s.get("text") or ""
            or_flag = " [OR]" if s.get("is_or_alternative") else ""
            parts.append(f"  {label} {text}{or_flag}".strip())
    if q.options:
        for i, o in enumerate(q.options):
            parts.append(f"  ({chr(65+i)}) {o}")
    return "\n".join(parts)


# ── LLM call — works with BOTH Gemini REST and OpenAI ────────────────────────
def _call_llm(prompt: str, strong: bool = False) -> str:
    """Route to the active provider. Returns raw JSON string."""
    provider = llm._provider

    if provider == "gemini":
        return _call_gemini(prompt, strong)
    elif provider == "openai":
        return _call_openai(prompt, strong)
    else:
        raise RuntimeError("No LLM provider configured. Set GEMINI_API_KEY or OPENAI_API_KEY in .env")


def _call_gemini(prompt: str, strong: bool = False) -> str:
    import requests as _requests

    system = _system_prompt()
    full_prompt = system + "\n\nQuestion to solve:\n" + prompt

    model = settings.gemini_strong_model if strong else settings.gemini_model
    key = settings.gemini_api_key

    # Model fallback list
    candidates = [model, "gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-3.5-flash-lite"]
    # Deduplicate while preserving order
    seen = set()
    model_list = []
    for m in candidates:
        m = m.replace("models/", "")
        if m not in seen:
            seen.add(m)
            model_list.append(m)

    payload = {
        "contents": [{"parts": [{"text": full_prompt}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "temperature": 0.2,
            "maxOutputTokens": 1024,
        },
    }

    last_exc = None
    for model_id in model_list:
        for attempt in range(2):
            try:
                resp = _requests.post(
                    f"https://generativelanguage.googleapis.com/v1beta/models/{model_id}:generateContent?key={key}",
                    headers={"Content-Type": "application/json"},
                    json=payload,
                    timeout=60,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    return data["candidates"][0]["content"]["parts"][0]["text"]
                elif resp.status_code in (429, 503):
                    last_exc = RuntimeError(f"Gemini {model_id} {resp.status_code}")
                    time.sleep(3)
                    break  # try next model
                else:
                    err = resp.json().get("error", {}).get("message", resp.text[:200])
                    last_exc = RuntimeError(f"Gemini {resp.status_code}: {err}")
                    break
            except Exception as exc:
                last_exc = exc
                if attempt == 0:
                    time.sleep(2)

    raise last_exc or RuntimeError("All Gemini models failed")


def _call_openai(prompt: str, strong: bool = False) -> str:
    model = settings.openai_strong_model if strong else settings.openai_model
    last_exc = None
    for attempt in range(3):
        try:
            resp = llm._openai_client.responses.create(
                model=model,
                instructions=_system_prompt(),
                input=prompt,
                text={"format": {"type": "json_object"}},
            )
            return resp.output_text
        except Exception as exc:
            last_exc = exc
            if attempt < 2:
                time.sleep(4 * (attempt + 1))
    raise last_exc


# ── Response parsing ──────────────────────────────────────────────────────────
def _fix_latex_escaping(text: str) -> str:
    """Fix JSON-parsed LaTeX: double-escaped backslashes from Gemini JSON."""
    # Gemini JSON over-escapes: \\\\frac → \\frac → \frac
    prev = None
    while prev != text:
        prev = text
        text = re.sub(r'\\\\([a-zA-Z()\[\]])', r'\\\1', text)
    # $$...$$ → \[...\]
    text = re.sub(r'\$\$(.+?)\$\$', r'\\[\1\\]', text, flags=re.S)
    # $...$ → \(...\)
    text = re.sub(r'(?<!\$)\$(?!\$)(.+?)(?<!\$)\$(?!\$)', r'\\(\1\\)', text, flags=re.S)
    return text


def _parse_response(raw: str) -> dict:
    """Parse LLM JSON output tolerantly."""
    text = raw.strip()
    # Strip markdown fences
    text = re.sub(r'^```[a-zA-Z]*\s*', '', text)
    text = re.sub(r'\s*```$', '', text)

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r'\{.*\}', text, re.S)
        if m:
            try:
                data = json.loads(m.group(0))
            except json.JSONDecodeError:
                return {"steps": [text], "final_answer": text, "correct_option": None}
        else:
            return {"steps": [text], "final_answer": text, "correct_option": None}

    # Gemini sometimes double-encodes: json.loads returns a string that is itself JSON
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except json.JSONDecodeError:
            return {"steps": [data], "final_answer": data, "correct_option": None}

    steps = data.get("steps") or []
    if isinstance(steps, str):
        steps = [steps]

    # Detect double-wrapped content: steps is a 1-element list whose sole entry
    # is itself a JSON object string (the whole response got stored as a step).
    if (len(steps) == 1 and isinstance(steps[0], str)
            and steps[0].strip().startswith('{')):
        try:
            inner = json.loads(steps[0])
            if isinstance(inner, dict) and ("steps" in inner or "final_answer" in inner):
                data = inner
                steps = data.get("steps") or []
                if isinstance(steps, str):
                    steps = [steps]
        except json.JSONDecodeError:
            pass

    final = data.get("final_answer") or (steps[-1] if steps else "")

    # If final_answer is itself a JSON blob, unwrap it
    if isinstance(final, str) and final.strip().startswith('{'):
        try:
            inner = json.loads(final)
            if isinstance(inner, dict):
                final = inner.get("final_answer") or final
                if not steps:
                    steps = inner.get("steps") or []
        except json.JSONDecodeError:
            pass

    return {
        "steps": [_fix_latex_escaping(str(s)) for s in steps],
        "final_answer": _fix_latex_escaping(str(final)),
        "correct_option": data.get("correct_option") or None,
    }


# ── Public API ────────────────────────────────────────────────────────────────
def generate_answer_for_question(q: Question) -> dict:
    """Solve one question and return the answer content dict."""
    prompt = _question_prompt(q)
    strong = _is_math_question(q)
    raw = _call_llm(prompt, strong)
    return _parse_response(raw)


def generate_answers_for_collection(db: Session, collection_id: int) -> dict:
    """Generate answers for all questions in a collection."""
    gen = db.query(AnswerGeneration).filter(
        AnswerGeneration.collection_id == collection_id
    ).first()
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
        .filter(Question.status.in_(["approved", "review", "pending"]))
        .all()
    )
    gen.total = len(questions)
    db.commit()

    errors = 0
    for i, q in enumerate(questions):
        # Skip if already answered
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
            existing.status = "generated"
            existing.error = None
        except Exception as exc:
            if existing is None:
                existing = Answer(question_id=q.id)
                db.add(existing)
            existing.status = "error"
            existing.error = str(exc)[:500]
            errors += 1

        gen.completed = i + 1
        db.commit()
        time.sleep(0.5)  # gentle pacing

    gen.status = "done"
    gen.error = f"{errors} failed" if errors else None
    db.commit()
    return {
        "status": "done",
        "total": gen.total,
        "completed": gen.completed,
        "errors": errors,
    }
