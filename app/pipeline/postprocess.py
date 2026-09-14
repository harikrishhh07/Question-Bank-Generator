import re

LABEL_RE = re.compile(r"^\s*(\(?[a-zA-Z][a-zA-Z0-9.]*\)?[\.\)])\s*(.*)$", re.S)
_HEADER_RE = re.compile(r"(\d+)\s*[x×X]\s*(\d+)\s*=")
PART_RE = re.compile(r"PART\s*[-–—:]?\s*([A-Ca-c])\s*")
MAX_MARKS_RE = re.compile(r"Max\.?\s*Marks?\s*[:–—]?\s*(\d+)", re.I)
_NUM_PREFIX_RE = re.compile(r"^\s*\d+\s*[\.\)]\s*")


def strip_number_prefix(text: str) -> str:
    """Remove a leading question-number prefix like '22.' or '21.' from question text."""
    return _NUM_PREFIX_RE.sub("", text or "", count=1)


_MATH_CHAR_RE = re.compile(r"\\[a-zA-Z]+|[{}_^]|[∫∑√π∞∇×±−]")
_MATH_TOKEN_RE = re.compile(r"[\\{}_^]|∫|∑|√|π|∞|∇|×|±")


def wrap_bare_math(text: str) -> str:
    """Wrap bare LaTeX runs in \\( ... \\) so KaTeX renders them.

    Leaves already-delimited math untouched. Operates token-by-token so plain
    words in mixed sentences are not rendered as math.
    """
    if not text or "\\(" in text or "\\[" in text:
        return text
    if not re.search(r"\\[a-zA-Z]+", text):
        return text

    tokens = text.split(" ")
    out: list[str] = []
    buf: list[str] = []

    def flush() -> None:
        if buf:
            out.append("\\(" + " ".join(buf) + "\\)")
            buf.clear()

    for tok in tokens:
        if _MATH_TOKEN_RE.search(tok):
            buf.append(tok)
        else:
            flush()
            out.append(tok)
    flush()
    return " ".join(out)


def parse_part_structure(texts: list[str]) -> dict:
    """Parse PART headers and their marks spans directly from page text.

    Returns {"order": [letters...], "defaults": {part: per-question marks}, "counts": {part: count}}.
    """
    markers: list[tuple[str, int, int, int | None, int | None]] = []
    for pi, text in enumerate(texts or []):
        if not text:
            continue
        for m in PART_RE.finditer(text):
            letter = m.group(1).upper()
            count = per_q = None
            span = _HEADER_RE.search(text, m.end())
            if span and span.start() - m.end() < 300:
                count, per_q = int(span.group(1)), int(span.group(2))
            markers.append((letter, pi, m.start(), count, per_q))

    order: list[str] = []
    defaults: dict[str, int] = {}
    counts: dict[str, int] = {}
    for letter, _pi, _pos, count, per_q in markers:
        if order and order[-1] == letter:
            if per_q:
                defaults[letter] = per_q
            if count:
                counts[letter] = count
            continue
        order.append(letter)
        if per_q:
            defaults[letter] = per_q
        if count:
            counts[letter] = count
    return {"order": order, "defaults": defaults, "counts": counts}


def compute_part_map_from_text(texts: list[str], questions: list[dict]) -> tuple[dict, dict]:
    """Derive question-number -> part and part -> default marks from page text."""
    structure = parse_part_structure(texts)
    order = structure["order"]
    defaults = structure["defaults"]
    counts = structure["counts"]

    numbers = sorted({_to_int(q.get("number")) for q in questions if _to_int(q.get("number")) is not None})
    if not numbers or not order:
        return {}, defaults

    contiguous = numbers[0] == 1 and numbers == list(range(1, len(numbers) + 1))
    if not contiguous:
        return {}, defaults

    num_map: dict[int, str] = {}
    idx = 0
    for i, letter in enumerate(order):
        count = counts.get(letter)
        if i == len(order) - 1:
            for n in numbers[idx:]:
                num_map[n] = letter
        else:
            for n in numbers[idx : idx + count] if count else []:
                num_map[n] = letter
        idx += count or 0

    return num_map, defaults


def _to_int(number) -> int | None:
    try:
        return int(str(number).split(".")[0])
    except (ValueError, TypeError):
        return None


def _label_letter(label: str | None) -> str:
    if not label:
        return ""
    m = re.match(r"\(?([a-zA-Z])", label)
    return m.group(1).lower() if m else ""


def compute_part_map(sections: list[dict], questions: list[dict]) -> tuple[dict, dict]:
    """Derive question-number -> part mapping and part -> default marks from section headers.

    Section header format e.g. "PART - A (20 x 1 = 20 Marks)" -> 20 questions, 1 mark each.
    Returns (num_to_part, part_default_marks).
    """
    counts: list[tuple[str, int]] = []
    part_defaults: dict[str, int] = {}
    for sec in sections:
        part = str(sec.get("part", "")).upper()
        header = str(sec.get("header", "")) or ""
        m = _HEADER_RE.search(header)
        if m:
            count = int(m.group(1))
            per_q = int(m.group(2))
            part_defaults[part] = per_q
            if part and count:
                counts.append((part, count))
        else:
            # fall back to VLM-provided default marks (no header parse)
            dmarks = sec.get("default_marks")
            if part and dmarks:
                part_defaults[part] = int(dmarks)
            if part and dmarks:
                counts.append((part, 0))

    numbers = sorted({_to_int(q.get("number")) for q in questions if _to_int(q.get("number")) is not None})
    if not numbers:
        return {}, part_defaults

    # Only trust numbering-derived parts if the numbering is contiguous from 1.
    contiguous = numbers[0] == 1 and numbers == list(range(1, len(numbers) + 1))
    if not contiguous:
        return {}, part_defaults

    num_map: dict[int, str] = {}
    idx = 0
    for i, (part, count) in enumerate(counts):
        if i == len(counts) - 1:
            # last part absorbs the remainder (e.g. "1 x 15" but 2 questions listed)
            for n in numbers[idx:]:
                num_map[n] = part
        else:
            for n in numbers[idx : idx + count]:
                num_map[n] = part
        idx += count

    return num_map, part_defaults


def fix_parts(questions: list[dict], num_map: dict) -> None:
    for q in questions:
        n = _to_int(q.get("number"))
        if n is None or n not in num_map:
            continue
        expected = num_map[n]
        current = str(q.get("part", "")).upper()
        if current != expected:
            if current:
                q.setdefault("flags", []).append(
                    {
                        "level": "info",
                        "code": "PART_CORRECTED",
                        "reason": f"Part corrected from '{current}' to '{expected}' using section question ranges.",
                    }
                )
            q["part"] = expected


def apply_marks_defaults(questions: list[dict], part_defaults: dict) -> None:
    for q in questions:
        part = str(q.get("part", "")).upper()
        dflt = part_defaults.get(part)
        if not dflt:
            continue
        if q.get("marks") != dflt:
            original = q.get("marks")
            q["marks"] = dflt
            if original is not None and original != dflt:
                q.setdefault("flags", []).append(
                    {
                        "level": "info",
                        "code": "MARKS_OVERRIDE",
                        "reason": f"Marks set to part default {dflt} (VLM read {original}, likely the BL/CO/PO table).",
                    }
                )


def split_label(text: str) -> tuple[str | None, str]:
    """Split a leading sub-label like 'a.', 'b.', 'a.i.', '(a)' from question text.

    Requires the label to end with '.' or ')' so plain words like "a function" are not matched.
    """
    text = strip_number_prefix(text)
    m = LABEL_RE.match(text or "")
    if m:
        label, rest = m.group(1).strip(), m.group(2).strip()
        if len(label) <= 8 and re.match(r"^\(?[a-zA-Z][a-zA-Z0-9.]*[\.\)]$", label):
            return label, rest
    return None, (text or "")


def _infer_label(prev_label: str | None, index: int) -> str:
    """Infer the next sub label (b, c, ...) after a lettered label."""
    letter = _label_letter(prev_label)
    if letter and len(letter) == 1:
        next_letter = chr(ord(letter) + 1)
        return f"{next_letter}."
    return f"({index + 1})"


def coalesce_subs(questions: list[dict]) -> list[dict]:
    """Merge consecutive questions sharing the same number into one question with subquestions.

    Handles 21.a / 21.b (OR alternatives) and 25.a.i / 25.a.ii (sub-parts), even when
    the second part has no explicit label.
    """
    out: list[dict] = []
    for q in questions:
        q = dict(q)
        n = _to_int(q.get("number"))
        if out and n is not None and _to_int(out[-1].get("number")) == n and not q.get("is_continuation"):
            prev = out[-1]
            if not (q.get("text") or "").strip():
                prev.setdefault("flags", []).append(
                    {"level": "review", "code": "EMPTY_SUB", "reason": f"Sub-part of question {prev.get('number')} had no extracted text."}
                )
                prev["continue_next"] = prev.get("continue_next") or q.get("continue_next")
                prev["pages"] = sorted(set(prev.get("pages", []) + q.get("pages", [])))
                continue
            label, rest = split_label(q.get("text", ""))
            if label:
                sub = {
                    "label": label,
                    "text": rest,
                    "marks": q.get("marks"),
                    "is_or_alternative": False,
                }
                prev_subs = prev.get("subs") or []
                prev_letter = _label_letter(prev_subs[-1].get("label")) if prev_subs else _label_letter(label)
                if prev_subs and prev_letter and _label_letter(label) != prev_letter:
                    sub["is_or_alternative"] = True
                prev.setdefault("subs", []).append(sub)
            else:
                # no label on the continuation -> infer the next letter, treat as OR alternative
                prev_subs = prev.get("subs") or []
                inferred = _infer_label(prev_subs[-1].get("label") if prev_subs else None, len(prev_subs))
                prev.setdefault("subs", []).append(
                    {
                        "label": inferred,
                        "text": q.get("text", ""),
                        "marks": q.get("marks"),
                        "is_or_alternative": bool(prev_subs),
                    }
                )
            prev["continue_next"] = prev.get("continue_next") or q.get("continue_next")
            prev["pages"] = sorted(set(prev.get("pages", []) + q.get("pages", [])))
            continue
        out.append(q)
    return out


def normalize_leading_subs(questions: list[dict]) -> list[dict]:
    """Convert a question whose text begins with a sub-label into sub[0].

    e.g. number 23, text "a. Find the laplace transform ..." -> subs=[{label:"a.", text:"Find..."}].
    """
    out = []
    for q in questions:
        q = dict(q)
        if not q.get("subs") and q.get("text"):
            text = strip_number_prefix(q["text"])
            label, rest = split_label(text)
            if label:
                q["text"] = ""
                q["subs"] = [
                    {
                        "label": label,
                        "text": rest,
                        "marks": q.get("marks"),
                        "is_or_alternative": False,
                    }
                ]
        out.append(q)
    return out


_OPTION_LIKE_RE = re.compile(r"\s*\(([A-Da-d])\)\s*(?=[A-Z0-9(])")
_PLACEHOLDER_OPTIONS = {"...", "", ".", "..", "……"}


def strip_inline_option_run(questions: list[dict]) -> list[dict]:
    """Remove a trailing '(A) ... (B) ... (C) ... (D) ...' run from question text
    when the question has its own options array (the VLM sometimes duplicates
    options into the question text)."""
    for q in questions:
        opts = q.get("options") or []
        text = q.get("text") or ""
        if not opts or not text:
            continue
        # find the first "(A)" that begins an option run
        m = re.search(r"\(A\)", text)
        if not m:
            continue
        head = text[: m.start()]
        tail = text[m.start() :]
        # only strip if the tail actually looks like an option run (has (A) and (B))
        if "(B)" in tail:
            q["text"] = re.sub(r"\s+", " ", head).strip()
    return questions


def clean_options(questions: list[dict]) -> list[dict]:
    """Remove fabricated/phantom options.

    The VLM frequently mistakes the BL/CO/PO weightage table columns for MCQ
    answer options, producing options like "(A) ... (B) ..." on descriptive
    questions, or empty "(A) (B) (C) (D)". This strips those.

    Also strips "(A)...(D)" option-like fragments from the question text when
    the question has a populated options array (double-rendering).
    """
    for q in questions:
        opts = q.get("options") or []
        if not opts:
            continue

        cleaned = [str(o).strip() for o in opts]
        part = str(q.get("part", "")).upper()

        # all empty or placeholder -> phantom
        if all(o in _PLACEHOLDER_OPTIONS for o in cleaned):
            q["options"] = []
            q.setdefault("flags", []).append(
                {"level": "review", "code": "OPTIONS_STRIPPED", "reason": f"Question {q.get('number')} had only placeholder/empty options (BL/CO/PO table artifact) and was cleaned."}
            )
            continue

        # descriptive part (B/C) with a mix of placeholder and table-fragment options
        if part in {"B", "C"}:
            non_placeholder = [o for o in cleaned if o not in _PLACEHOLDER_OPTIONS]
            if non_placeholder and all(len(o) < 12 for o in non_placeholder):
                q["options"] = []
                q.setdefault("flags", []).append(
                    {"level": "review", "code": "OPTIONS_STRIPPED", "reason": f"Descriptive question {q.get('number')} (part {part}) had table-artifact options and was cleaned."}
                )
                continue

        # strip option-like fragments from the text when options are present
        if q.get("text"):
            stripped = _OPTION_LIKE_RE.sub("", q["text"])
            stripped = re.sub(r"\s+", " ", stripped).strip()
            if stripped != q["text"]:
                q["text"] = stripped

    return questions


def repair_math_delimiters(questions: list[dict]) -> list[dict]:
    r"""Fix mismatched \( \) / \[ \] delimiters in question text, options and subs."""
    for q in questions:
        if q.get("text"):
            q["text"] = _repair_delims(q["text"])
        if q.get("options"):
            q["options"] = [_repair_delims(o) for o in q["options"]]
        if q.get("subs"):
            for s in q["subs"]:
                if s.get("text"):
                    s["text"] = _repair_delims(s["text"])
    return questions


def _repair_delims(text: str) -> str:
    if not text:
        return text
    # Merge broken pairs like "\frac{...}{13\) + 5 \(\sin}" where the VLM split
    # one math expression into two groups with a stray marker in the middle.
    if re.search(r"\\[a-zA-Z]+", text):
        text = re.sub(r"\\\)([^\\()]{0,25}?)\\\( ", r"\1 ", text)
        text = re.sub(r"\\\)([^\\()]{0,25}?)\\\(", r"\1", text)
    out = []
    open_pending = 0
    i, n = 0, len(text)
    while i < n:
        if text.startswith("\\(", i):
            out.append("\\(")
            open_pending += 1
            i += 2
        elif text.startswith("\\)", i):
            if open_pending > 0:
                open_pending -= 1
                out.append("\\)")
            # else: orphan closer -> drop it
            i += 2
        elif text.startswith("\\[", i):
            out.append("\\[")
            open_pending += 1
            i += 2
        elif text.startswith("\\]", i):
            if open_pending > 0:
                open_pending -= 1
                out.append("\\]")
            i += 2
        else:
            out.append(text[i])
            i += 1
    for _ in range(open_pending):
        out.append("\\)")
    return "".join(out)
