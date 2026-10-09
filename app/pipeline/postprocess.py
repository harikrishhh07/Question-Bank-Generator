from __future__ import annotations

import re

LABEL_RE = re.compile(r"^\s*(\(?[a-zA-Z][a-zA-Z0-9.]*\)?[\.\)])\s*(.*)$", re.S)
_HEADER_RE = re.compile(r"(\d+)\s*[x×X]\s*(\d+)\s*=")

# ---- Text spacing normalisation ----
# Matches a run of single characters separated by single spaces, e.g.
# "d i f f e r e n t i a t e" — an OCR artefact where the scanner treats each
# glyph as its own word.  We detect such runs and collapse them.
_SPACED_WORD_RE = re.compile(
    r"(?:^|(?<=\s))"        # start of string or after whitespace
    r"([A-Za-z0-9]"         # first isolated character
    r"(?:\s[A-Za-z0-9]){2,})"  # followed by 2+ more "space char" pairs
    r"(?=\s|$)"             # end of string or followed by whitespace
)


def _collapse_run(m: re.Match) -> str:
    """Remove the spaces from a matched isolated-character run."""
    return m.group(1).replace(" ", "")


# Known fused phrases that appear in exam question OCR output.
# Each entry maps the exact fused string -> the correctly spaced version.
# Longer entries must come before shorter ones so we replace the most specific first.
_FUSED_PHRASE_MAP: list[tuple[re.Pattern, str]] = [
    # "c so that the vector / function / …" variants
    (re.compile(r'\bcsothatthevector\b', re.I), 'c so that the vector'),
    (re.compile(r'\bcsothatthe\b', re.I), 'c so that the'),
    (re.compile(r'\bcsothat\b', re.I), 'c so that'),
    (re.compile(r'\bsothatthe\b', re.I), 'so that the'),
    (re.compile(r'\bsothat\b', re.I), 'so that'),
    # "onto the point …"
    (re.compile(r'\bontothepointw\b', re.I), 'onto the point w'),
    (re.compile(r'\bontothepoint\b', re.I), 'onto the point'),
    (re.compile(r'\bontothe\b', re.I), 'onto the'),
    (re.compile(r'\bontoto\b', re.I), 'onto to'),
    # "expand as Laurent's series about z"
    (re.compile(r"expandasLaurent'sseriesabout", re.I), "expand as Laurent's series about"),
    (re.compile(r"expandsasLaurent'sseriesabout", re.I), "expands as Laurent's series about"),
    (re.compile(r'expandasLaurentseriesabout', re.I), "expand as Laurent's series about"),
    (re.compile(r'\bexpandas\b', re.I), 'expand as'),
    (re.compile(r'\bseriesabout\b', re.I), 'series about'),
    (re.compile(r'\baboutz\b', re.I), 'about z'),
    (re.compile(r'\baboutw\b', re.I), 'about w'),
    # "where c is the circle / region"
    (re.compile(r'\bwherecisthecircle\b', re.I), 'where c is the circle'),
    (re.compile(r'\bwherecisthe\b', re.I), 'where c is the'),
    (re.compile(r'\bwherecis\b', re.I), 'where c is'),
    # "where u …" / "where u - v" etc  (single trailing letter)
    (re.compile(r'\bwhereu\b', re.I), 'where u'),
    (re.compile(r'\bwherev\b', re.I), 'where v'),
    # "dz where c is"
    (re.compile(r'\bdzwhere\b', re.I), 'dz where'),
    # generic "where the / where a / where it"
    (re.compile(r'\bwherethe\b', re.I), 'where the'),
    (re.compile(r'\bwherethere\b', re.I), 'where there'),
    # "valid in" / "valid for"
    (re.compile(r'\bvalidin\b', re.I), 'valid in'),
    (re.compile(r'\bvalidfor\b', re.I), 'valid for'),
    # "find the" / "show that" at word start after boundary
    (re.compile(r'\bfindthe\b', re.I), 'find the'),
    (re.compile(r'\bshowthat\b', re.I), 'show that'),
    (re.compile(r'\bsuchthat\b', re.I), 'such that'),
    (re.compile(r'\bgiven that\b', re.I), 'given that'),
    (re.compile(r'\bgiventhat\b', re.I), 'given that'),
    # "also find" / "also show"
    (re.compile(r'\balsofind\b', re.I), 'also find'),
    (re.compile(r'\balsoshow\b', re.I), 'also show'),
]


def _split_fused_words(text: str) -> str:
    """Replace known fused word sequences with correctly spaced versions.

    Uses a fixed lookup table of phrases commonly fused in exam OCR output.
    Conservative by design — only fixes known patterns to avoid mangling
    legitimate technical terms (e.g. 'irrotational', 'conservative').
    """
    for pattern, replacement in _FUSED_PHRASE_MAP:
        text = pattern.sub(replacement, text)
    return text


def normalize_text_spacing(text: str) -> str:
    """Normalize spacing issues in OCR-extracted question text.

    1. Collapse character-fragmented words ('d i f f e r e n t i a t e' -> 'differentiate').
    2. Insert space at word/math boundaries: 'ontothepointw\\(' -> 'onto the point w \\('.
    3. Collapse multiple consecutive spaces into one.
    4. Remove stray spaces immediately before punctuation.
    5. Ensure a single space after commas, colons, semicolons when missing.
    6. Strip leading/trailing whitespace.
    Math delimiters (\\( ... \\)) are left untouched.
    """
    if not text:
        return text

    # ── Pass 1: insert space between closing math delimiter and a following word ──
    # e.g. \)ontothepointw  →  \) onto the point w
    # e.g. \)whereu         →  \) where u
    text = re.sub(r'(\\\))([A-Za-z])', r'\1 \2', text)
    # insert space between a word/digit and an opening math delimiter
    # e.g. csothatthevector\(  →  cso that the vector \(
    text = re.sub(r'([A-Za-z0-9])(\\\()', r'\1 \2', text)
    # also handle \] and \[
    text = re.sub(r'(\\\])([A-Za-z])', r'\1 \2', text)
    text = re.sub(r'([A-Za-z0-9])(\\\[)', r'\1 \2', text)

    def _process_plain(segment: str) -> str:
        # Step 1: collapse isolated-character runs (e.g. "d i f f e r")
        segment = _SPACED_WORD_RE.sub(_collapse_run, segment)
        # Step 2: split run-together words using camelCase/word-boundary heuristic.
        # Handles "ontothepointw", "expandasLaurent", "csothatthevector" etc.
        # Strategy: insert a space before each uppercase letter in the middle of a
        # lower→upper transition, and before common English function words when
        # they appear as a prefix of a longer string.
        segment = _split_fused_words(segment)
        # Step 3: multiple spaces -> one
        segment = re.sub(r" {2,}", " ", segment)
        # Step 4: remove space before punctuation
        segment = re.sub(r" +([,;:!?.\)\]])", r"\1", segment)
        # Step 5: add space after punctuation if missing
        segment = re.sub(r"([,;:])(?=[^\s\\])", r"\1 ", segment)
        return segment

    # Split around math regions to leave LaTeX untouched
    parts: list[tuple[str, str]] = []
    i = 0
    n = len(text)
    while i < n:
        found_math = False
        for open_d, close_d in (("\\(", "\\)"), ("\\[", "\\]")):
            if text.startswith(open_d, i):
                end = text.find(close_d, i + len(open_d))
                if end != -1:
                    parts.append(("math", text[i : end + len(close_d)]))
                    i = end + len(close_d)
                    found_math = True
                    break
        if found_math:
            continue
        j = i + 1
        while j < n:
            hit = False
            for open_d, _ in (("\\(", "\\)"), ("\\[", "\\]")):
                if text.startswith(open_d, j):
                    hit = True
                    break
            if hit:
                break
            j += 1
        parts.append(("plain", text[i:j]))
        i = j

    result = "".join(
        seg if kind == "math" else _process_plain(seg)
        for kind, seg in parts
    )
    return result.strip()
PART_RE = re.compile(r"PART\s*[-–—:]?\s*([A-Ca-c])\s*")
MAX_MARKS_RE = re.compile(r"Max\.?\s*Marks?\s*[:–—]?\s*(\d+)", re.I)
_NUM_PREFIX_RE = re.compile(r"^\s*\d+\s*[\.\)]\s*")


def strip_number_prefix(text: str) -> str:
    """Remove a leading question-number prefix like '22.' or '21.' from question text."""
    return _NUM_PREFIX_RE.sub("", text or "", count=1)


_MATH_CHAR_RE = re.compile(r"\\[a-zA-Z]+|[{}_^]|[∫∑√π∞∇×±−]")
_MATH_TOKEN_RE = re.compile(r"[\\{}_^]|∫|∑|√|π|∞|∇|×|±")


# ---- Unicode math symbol → LaTeX command map ----
_UNICODE_MATH_MAP = [
    # Greek letters
    ("α", r"\alpha"), ("β", r"\beta"), ("γ", r"\gamma"), ("δ", r"\delta"),
    ("ε", r"\epsilon"), ("ζ", r"\zeta"), ("η", r"\eta"), ("θ", r"\theta"),
    ("ι", r"\iota"), ("κ", r"\kappa"), ("λ", r"\lambda"), ("μ", r"\mu"),
    ("ν", r"\nu"), ("ξ", r"\xi"), ("π", r"\pi"), ("ρ", r"\rho"),
    ("σ", r"\sigma"), ("τ", r"\tau"), ("υ", r"\upsilon"), ("φ", r"\phi"),
    ("χ", r"\chi"), ("ψ", r"\psi"), ("ω", r"\omega"),
    ("Γ", r"\Gamma"), ("Δ", r"\Delta"), ("Θ", r"\Theta"), ("Λ", r"\Lambda"),
    ("Ξ", r"\Xi"), ("Π", r"\Pi"), ("Σ", r"\Sigma"), ("Υ", r"\Upsilon"),
    ("Φ", r"\Phi"), ("Ψ", r"\Psi"), ("Ω", r"\Omega"),
    # Operators
    ("×", r"\times"), ("÷", r"\div"), ("±", r"\pm"), ("∓", r"\mp"),
    ("·", r"\cdot"), ("∘", r"\circ"), ("⊗", r"\otimes"), ("⊕", r"\oplus"),
    # Relations
    ("≤", r"\leq"), ("≥", r"\geq"), ("≠", r"\neq"), ("≈", r"\approx"),
    ("≡", r"\equiv"), ("∝", r"\propto"), ("∼", r"\sim"), ("≅", r"\cong"),
    # Calculus / analysis
    ("∫", r"\int"), ("∬", r"\iint"), ("∭", r"\iiint"),
    ("∑", r"\sum"), ("∏", r"\prod"),
    ("∂", r"\partial"), ("∇", r"\nabla"), ("∆", r"\Delta"),
    ("√", r"\sqrt"), ("∞", r"\infty"),
    # Logic / sets
    ("∈", r"\in"), ("∉", r"\notin"), ("⊂", r"\subset"), ("⊃", r"\supset"),
    ("⊆", r"\subseteq"), ("⊇", r"\supseteq"), ("∩", r"\cap"), ("∪", r"\cup"),
    ("∅", r"\emptyset"), ("∀", r"\forall"), ("∃", r"\exists"),
    ("∧", r"\wedge"), ("∨", r"\vee"), ("¬", r"\neg"),
    # Arrows
    ("→", r"\rightarrow"), ("←", r"\leftarrow"), ("↔", r"\leftrightarrow"),
    ("⇒", r"\Rightarrow"), ("⇐", r"\Leftarrow"), ("⇔", r"\Leftrightarrow"),
    ("↑", r"\uparrow"), ("↓", r"\downarrow"),
    # Misc
    ("°", r"^{\circ}"), ("′", r"'"), ("″", r"''"),
    ("ℝ", r"\mathbb{R}"), ("ℤ", r"\mathbb{Z}"), ("ℕ", r"\mathbb{N}"),
    ("ℂ", r"\mathbb{C}"), ("ℚ", r"\mathbb{Q}"),
    ("−", r"-"),  # en-dash used as minus → ASCII minus
]

# Pattern that detects whether a string contains Unicode math that needs conversion
_UNICODE_MATH_CHARS = re.compile(
    r"[αβγδεζηθικλμνξπρστυφχψωΓΔΘΛΞΠΣΥΦΨΩ×÷±∓·∘⊗⊕≤≥≠≈≡∝∼≅∫∬∭∑∏∂∇√∞∈∉⊂⊃⊆⊇∩∪∅∀∃∧∨¬→←↔⇒⇐⇔↑↓°ℝℤℕℂℚ−]"
)


def unicode_math_to_latex(text: str) -> str:
    """Convert Unicode math symbols in text to their LaTeX equivalents.

    Only converts symbols that appear outside already-delimited math spans,
    so \\(\\pi\\) is left alone but bare π → \\pi.
    """
    if not text or not _UNICODE_MATH_CHARS.search(text):
        return text

    # Split into math-delimited regions and plain regions, process plain only.
    # Handles \(...\) and \[...\] delimiters.
    parts: list[str] = []
    i, n = 0, len(text)
    while i < n:
        for delim_open, delim_close in (("\\(", "\\)"), ("\\[", "\\]")):
            if text.startswith(delim_open, i):
                end = text.find(delim_close, i + len(delim_open))
                if end != -1:
                    parts.append(text[i : end + len(delim_close)])
                    i = end + len(delim_close)
                    break
        else:
            parts.append(text[i])
            i += 1

    result_parts: list[str] = []
    for part in parts:
        if part.startswith("\\(") or part.startswith("\\["):
            result_parts.append(part)  # inside math — leave as-is
        else:
            for uni, latex in _UNICODE_MATH_MAP:
                part = part.replace(uni, latex)
            result_parts.append(part)

    converted = "".join(result_parts)

    # Now wrap any newly-created bare LaTeX commands (from the unicode conversion)
    # that are not yet inside delimiters.
    return _wrap_latex_outside_delimiters(converted)


def _wrap_latex_outside_delimiters(text: str) -> str:
    """Wrap contiguous LaTeX math runs outside existing \\(...\\) / \\[...\\] in \\(...\\).

    A "math run" is a maximal span of tokens where each token either:
      - starts with a backslash followed by a letter (LaTeX command), or
      - contains only math-valid characters: digits, operators +-=/<>*/^_{}|., braces
        and no whitespace (a "math atom").
    Plain words between runs break the run.
    """
    if not text:
        return text

    # Already fully delimited? skip.
    if not re.search(r"\\[a-zA-Z]", text):
        return text

    # Walk character-by-character, tracking regions
    segments: list[tuple[bool, str]] = []  # (is_delimited, content)
    i, n = 0, len(text)

    while i < n:
        # Check if we're entering a delimited math region
        matched_delim = False
        for open_d, close_d in (("\\(", "\\)"), ("\\[", "\\]")):
            if text.startswith(open_d, i):
                end = text.find(close_d, i + len(open_d))
                if end != -1:
                    segments.append((True, text[i : end + len(close_d)]))
                    i = end + len(close_d)
                    matched_delim = True
                    break
        if matched_delim:
            continue
        # Accumulate plain text
        j = i + 1
        while j < n:
            for open_d, _ in (("\\(", "\\)"), ("\\[", "\\]")):
                if text.startswith(open_d, j):
                    break
            else:
                j += 1
                continue
            break
        segments.append((False, text[i:j]))
        i = j

    out_parts: list[str] = []
    for is_delimited, content in segments:
        if is_delimited:
            out_parts.append(content)
        else:
            out_parts.append(_wrap_math_runs_in_plain(content))
    return "".join(out_parts)


_LATEX_CMD_RE = re.compile(r"\\[a-zA-Z]+")


def _wrap_math_runs_in_plain(text: str) -> str:
    r"""Wrap contiguous LaTeX-math spans in \(...\) within plain (non-delimited) text.

    Strategy: scan character by character.  A math run starts the moment we
    encounter a LaTeX command (\letter…).  Once inside a run, we stay in it as
    long as:
      - we are inside braces {} or parentheses () (depth > 0), OR
      - the next non-space character is a digit, operator (+-=/<>*^_|.), brace,
        paren, backslash, or another letter that follows a ^ or _ (subscript/
        superscript argument).
    A run ends at the first plain-word character (letter sequence not preceded
    by ^ _ or \) at depth 0, or a sentence-ending punctuation that closes a
    depth-0 context.
    """
    if not _LATEX_CMD_RE.search(text):
        return text  # nothing to do — no LaTeX commands present

    result: list[str] = []
    i = 0
    n = len(text)

    while i < n:
        # Special case: \begin{env}...\end{env} — wrap the whole environment
        if text.startswith("\\begin{", i):
            env_end_match = re.search(r"\\end\{[a-zA-Z*]+\}", text[i:])
            if env_end_match:
                env_block = text[i : i + env_end_match.end()]
                result.append("\\(" + env_block + "\\)")
                i += env_end_match.end()
                continue
        # Check for a LaTeX command start: backslash followed by letters
        if text[i] == "\\" and i + 1 < n and text[i + 1].isalpha():
            # Start of a math run — scan forward with bracket-awareness
            run_start = i
            depth_brace = 0
            depth_paren = 0
            j = i
            last_non_space = i

            while j < n:
                c = text[j]
                if c == "{":
                    depth_brace += 1
                    last_non_space = j
                    j += 1
                elif c == "}":
                    depth_brace = max(0, depth_brace - 1)
                    last_non_space = j
                    j += 1
                elif c == "(":
                    depth_paren += 1
                    last_non_space = j
                    j += 1
                elif c == ")":
                    if depth_paren > 0:
                        depth_paren -= 1
                        last_non_space = j
                        j += 1
                    else:
                        # Closing paren with no open — end the run before it
                        break
                elif c == "\\" and j + 1 < n and text[j + 1].isalpha():
                    # Another LaTeX command — continue the run
                    last_non_space = j
                    j += 1
                    while j < n and text[j].isalpha():
                        j += 1
                elif c == " " or c == "\t":
                    if depth_brace > 0 or depth_paren > 0:
                        # Inside braces/parens — spaces are part of the expression
                        j += 1
                    else:
                        # Peek ahead: if the next non-space token continues math, keep going
                        k = j
                        while k < n and text[k] in " \t":
                            k += 1
                        if k < n and text[k] in "0123456789+-=<>*/^_.\\|,!;":
                            # Next token is a math character → stay in run
                            j = k
                        elif k < n and text[k] == "\\" and k + 1 < n and text[k + 1].isalpha():
                            j = k  # another command
                        elif k < n and text[k].isalpha():
                            # Could be e^{...} or dx or single-letter variable — stay in run
                            # only if what follows the word is a math operator/brace/digit
                            m = k + 1
                            while m < n and text[m].isalpha():
                                m += 1
                            word_len = m - k
                            # skip spaces after the word to find the next non-space char
                            mm = m
                            while mm < n and text[mm] == " ":
                                mm += 1
                            next_after = text[mm] if mm < n else ""
                            if word_len <= 2 and next_after in "^_{([+-=0123456789":
                                j = k  # e.g. "e^{-x}", "y = 0", "dx^2"
                            else:
                                break
                        else:
                            # Space before a word/punctuation that ends the run
                            break
                elif c in "0123456789+-=<>*/^_.|,":
                    last_non_space = j
                    j += 1
                elif c.isalpha():
                    # Letters are part of the run inside braces/parens, after ^ _, or
                    # when they are a single-letter math variable followed by math chars
                    if depth_brace > 0 or depth_paren > 0:
                        last_non_space = j
                        j += 1
                    elif j > 0 and text[j - 1] in "^_":
                        last_non_space = j
                        j += 1
                    else:
                        # Check if it's a short math token: single/double letter followed
                        # by ^, _, {, (, +, =, or end of run context
                        k = j
                        while k < n and text[k].isalpha():
                            k += 1
                        word_len = k - j
                        # peek past any trailing spaces to find what follows
                        kk = k
                        while kk < n and text[kk] == " ":
                            kk += 1
                        next_ch = text[kk] if kk < n else ""
                        if word_len <= 2 and next_ch in "^_{(+-=*/0123456789":
                            # short math token like "e^{-x}", "dx^2", "y = 0"
                            last_non_space = k - 1
                            j = k
                        elif word_len == 1 and kk >= n:
                            # single letter at end of string — include it
                            last_non_space = k - 1
                            j = k
                        else:
                            # Multi-letter plain word — end the run before it
                            break
                else:
                    # Other character (punctuation etc.) — end the run
                    break

            run = text[run_start:j].strip()
            if run:
                result.append("\\(" + run + "\\)")
            else:
                result.append(text[run_start:j])
            i = j
        else:
            result.append(text[i])
            i += 1

    return "".join(result)


def wrap_bare_math(text: str) -> str:
    """Ensure all math in text is properly delimited for KaTeX.

    Pass 1: convert Unicode math symbols to LaTeX commands.
    Pass 2: wrap bare LaTeX runs (commands not yet inside \\(...\\) or \\[...\\]) in \\(...\\).
    Already-delimited regions are left untouched in both passes.
    """
    if not text:
        return text
    # Pass 1: Unicode → LaTeX
    text = unicode_math_to_latex(text)
    # Pass 2: wrap any remaining bare LaTeX commands
    text = _wrap_latex_outside_delimiters(text)
    return text


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
    # Increased lookahead from 25 to 120 chars to handle longer equation continuations.
    if re.search(r"\\[a-zA-Z]+", text):
        text = re.sub(r"\\\)([^\\()]{0,120}?)\\\( ", r"\1 ", text)
        text = re.sub(r"\\\)([^\\()]{0,120}?)\\\(", r"\1", text)
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
