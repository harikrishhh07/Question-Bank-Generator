from __future__ import annotations

import base64
import json
import re
import time
from pathlib import Path

from .config import settings


class LLM:
    """Vision-language model interface — supports OpenAI and Gemini.

    Provider selection:
      - If GEMINI_API_KEY is set in .env → uses Gemini (gemini-1.5-flash / gemini-1.5-pro)
      - Otherwise falls back to OpenAI (gpt-4o-mini / gpt-4o)
    """

    def __init__(self):
        self._provider: str = "none"
        self._openai_client = None
        self._gemini_configured = False

        if settings.gemini_api_key:
            try:
                from google import genai
                from google.genai import types
                import warnings
                warnings.filterwarnings("ignore", category=FutureWarning)
                self._genai_client = genai.Client(api_key=settings.gemini_api_key)
                self._genai_types = types
                self._provider = "gemini"
                self._gemini_configured = True
            except Exception:
                pass  # fall through to OpenAI

        if self._provider == "none" and settings.openai_api_key:
            from openai import OpenAI
            self._openai_client = OpenAI(api_key=settings.openai_api_key)
            self._provider = "openai"

    @property
    def available(self) -> bool:
        return self._provider != "none"

    def _encode_image(self, image_path: Path) -> str:
        return base64.b64encode(image_path.read_bytes()).decode("utf-8")

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def understand_page(self, image_path: Path, page_num: int = 0, total_pages: int = 1,
                        strong: bool = False) -> dict | None:
        """Send a page image to the VLM and return structured JSON."""
        if self._provider == "gemini":
            return self._understand_page_gemini(image_path, page_num, total_pages, strong)
        elif self._provider == "openai":
            return self._understand_page_openai(image_path, page_num, total_pages, strong)
        return None

    def understand_page_batch(self, page_paths: list[Path], strong: bool = False) -> list[dict]:
        results = []
        for i, path in enumerate(page_paths):
            result = self.understand_page(path, i, len(page_paths), strong)
            results.append(result or {})
        return results

    # ------------------------------------------------------------------
    # Gemini backend
    # ------------------------------------------------------------------

    def _understand_page_gemini(self, image_path: Path, page_num: int, total_pages: int,
                                 strong: bool) -> dict:
        import requests

        # Primary model with fallbacks in case of 503/overload
        if strong:
            model_candidates = ["gemini-3.5-flash", "gemini-3.5-flash-lite", "gemini-3.6-flash", "gemini-3.7-flash"]
        else:
            model_candidates = ["gemini-3.5-flash-lite", "gemini-3.5-flash", "gemini-3.6-flash"]

        primary = settings.gemini_strong_model if strong else settings.gemini_model
        if primary not in model_candidates:
            model_candidates.insert(0, primary)

        prompt = self._build_prompt(page_num, total_pages)
        b64 = self._encode_image(image_path)
        key = settings.gemini_api_key

        payload = {
            "contents": [{
                "parts": [
                    {"text": prompt},
                    {"inline_data": {"mime_type": "image/png", "data": b64}}
                ]
            }],
            "generationConfig": {
                "responseMimeType": "application/json",
                "temperature": 0.1,
            }
        }

        last_exc = None
        for model_id in model_candidates:
            model_id = model_id.replace("models/", "")
            for attempt in range(2):
                try:
                    resp = requests.post(
                        f"https://generativelanguage.googleapis.com/v1beta/models/{model_id}:generateContent?key={key}",
                        headers={"Content-Type": "application/json"},
                        json=payload,
                        timeout=120,
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        text = data["candidates"][0]["content"]["parts"][0]["text"]
                        return self._parse_json_response(text, page_num)
                    elif resp.status_code == 503:
                        # Overloaded — try next model
                        last_exc = RuntimeError(f"Gemini {model_id} 503 overloaded")
                        break
                    else:
                        err = resp.json().get("error", {}).get("message", resp.text[:200])
                        last_exc = RuntimeError(f"Gemini API {resp.status_code}: {err}")
                        break  # 4xx errors won't resolve with retry — try next model
                except Exception as exc:
                    last_exc = exc
                    if attempt < 1:
                        time.sleep(3)

        return self._error_result(str(last_exc), page_num)

    # ------------------------------------------------------------------
    # OpenAI backend
    # ------------------------------------------------------------------

    def _understand_page_openai(self, image_path: Path, page_num: int, total_pages: int,
                                 strong: bool) -> dict:
        model = settings.openai_strong_model if strong else settings.openai_model
        b64 = self._encode_image(image_path)
        prompt = self._build_prompt(page_num, total_pages)

        last_exc = None
        for attempt in range(3):
            try:
                response = self._openai_client.responses.create(
                    model=model,
                    input=[
                        {
                            "role": "user",
                            "content": [
                                {"type": "input_text", "text": prompt},
                                {"type": "input_image",
                                 "image_url": f"data:image/png;base64,{b64}",
                                 "detail": "high"},
                            ],
                        }
                    ],
                    text={"format": {"type": "json_object"}},
                )
                text = response.output_text
                break
            except Exception as exc:
                last_exc = exc
                if attempt < 2:
                    time.sleep(4 * (attempt + 1))
        else:
            return self._error_result(str(last_exc), page_num)

        return self._parse_json_response(text, page_num)

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------

    def _parse_json_response(self, text: str, page_num: int) -> dict:
        try:
            result = json.loads(text)
        except json.JSONDecodeError:
            m = re.search(r"\{.*\}", text, re.S)
            if m:
                try:
                    result = json.loads(m.group(0))
                except json.JSONDecodeError:
                    return self._error_result("JSON parse failed", page_num)
            else:
                return self._error_result("JSON parse failed", page_num)
        # Post-process every text field to fix LaTeX issues
        return self._postprocess_latex(result)

    def _postprocess_latex(self, result: dict) -> dict:
        """
        Comprehensive LaTeX post-processing pass applied to every VLM response.

        Fixes (in order):
        1. JSON triple/quadruple-escaped backslashes  \\\\frac → \\frac
        2. $$...$$ display math  → \\[...\\]
        3. $...$ inline math     → \\(...\\)
        4. Bare Unicode math symbols → \\( LaTeX \\)
        5. Bare LaTeX commands not yet wrapped → \\( cmd \\)
        6. Normalize malformed delimiters: \\( \\) with wrong spacing/nesting
        """
        for q in result.get('questions') or []:
            self._fix_q_latex(q)
        return result

    # ---- Unicode math → LaTeX mapping -----------------------------------
    _UNICODE_MAP = {
        '∫': r'\int', '∬': r'\iint', '∭': r'\iiint',
        '∑': r'\sum', '∏': r'\prod',
        '√': r'\sqrt', '∛': r'\sqrt[3]', '∜': r'\sqrt[4]',
        '∂': r'\partial', '∇': r'\nabla', '∞': r'\infty',
        '≤': r'\leq', '≥': r'\geq', '≠': r'\neq', '≈': r'\approx',
        '≡': r'\equiv', '≪': r'\ll', '≫': r'\gg',
        '∈': r'\in', '∉': r'\notin', '⊆': r'\subseteq', '⊂': r'\subset',
        '⊇': r'\supseteq', '⊃': r'\supset',
        '∪': r'\cup', '∩': r'\cap', '∅': r'\emptyset',
        '∀': r'\forall', '∃': r'\exists', '∄': r'\nexists',
        '∧': r'\wedge', '∨': r'\vee', '¬': r'\neg', '⊕': r'\oplus',
        '→': r'\rightarrow', '←': r'\leftarrow',
        '⇒': r'\Rightarrow', '⟹': r'\Longrightarrow',
        '⇔': r'\Leftrightarrow', '↔': r'\leftrightarrow',
        'α': r'\alpha', 'β': r'\beta', 'γ': r'\gamma', 'δ': r'\delta',
        'ε': r'\epsilon', 'ζ': r'\zeta', 'η': r'\eta', 'θ': r'\theta',
        'ι': r'\iota', 'κ': r'\kappa', 'λ': r'\lambda', 'μ': r'\mu',
        'ν': r'\nu', 'ξ': r'\xi', 'π': r'\pi', 'ρ': r'\rho',
        'σ': r'\sigma', 'τ': r'\tau', 'υ': r'\upsilon', 'φ': r'\phi',
        'χ': r'\chi', 'ψ': r'\psi', 'ω': r'\omega',
        'Γ': r'\Gamma', 'Δ': r'\Delta', 'Θ': r'\Theta', 'Λ': r'\Lambda',
        'Ξ': r'\Xi', 'Π': r'\Pi', 'Σ': r'\Sigma', 'Φ': r'\Phi',
        'Ψ': r'\Psi', 'Ω': r'\Omega',
        '±': r'\pm', '∓': r'\mp', '×': r'\times', '÷': r'\div',
        '·': r'\cdot', '°': r'^{\circ}',
        '⌈': r'\lceil', '⌉': r'\rceil', '⌊': r'\lfloor', '⌋': r'\rfloor',
        '∝': r'\propto', '∼': r'\sim', '≃': r'\simeq', '≅': r'\cong',
        '⊥': r'\perp', '∥': r'\parallel', '∠': r'\angle',
        '′': "'", '″': "''",
        '½': r'\frac{1}{2}', '⅓': r'\frac{1}{3}', '¼': r'\frac{1}{4}',
        '¾': r'\frac{3}{4}',
    }

    # Patterns that indicate a text fragment is almost certainly math
    _MATH_FRAG_RE = re.compile(
        r'(?:'
        r'\\[a-zA-Z]+'          # any \cmd
        r'|[a-zA-Z]\s*\^'       # x^
        r'|[a-zA-Z]\s*_'        # x_
        r'|\^\{[^}]+\}'         # ^{...}
        r'|_\{[^}]+\}'          # _{...}
        r'|\{[^}]+\}'           # {group}
        r'|d[a-z]/d[a-z]'       # dx/dy
        r'|[a-z]\([a-z]\)'      # f(x)
        r')'
    )

    def _fix_text_latex(self, text: str) -> str:
        """Apply all LaTeX fixes to a single text string."""
        if not text:
            return text

        # 1. Unescape quadruple/triple backslash (JSON over-escaping by Gemini)
        #    \\\\frac → \\frac → \frac
        prev = None
        while prev != text:
            prev = text
            text = re.sub(r'\\\\([a-zA-Z()\[\]])', r'\\\1', text)

        # 2. $$...$$ → \[...\] (display mode)
        text = re.sub(r'\$\$(.+?)\$\$', r'\\[\1\\]', text, flags=re.S)

        # 3. Normalise \[ \] spacing
        text = re.sub(r'\\\[\s*', r'\\[', text)
        text = re.sub(r'\s*\\\]', r'\\]', text)

        # 4. $...$ → \(...\) only outside existing delimiters
        text = self._convert_dollar_math(text)

        # 5. Convert plain-text derivative/fraction notation before unicode pass
        text = self._convert_plain_fractions(text)

        # 6. Unicode math symbols → LaTeX
        text = self._convert_unicode_math(text)

        # 7. Bare LaTeX commands sitting outside any delimiter → wrap them
        text = self._wrap_bare_commands(text)

        # 8. Fix unclosed/malformed delimiters
        text = self._repair_delimiters(text)

        return text

    def _convert_plain_fractions(self, text: str) -> str:
        """Convert plain-text calculus/fraction notation to LaTeX outside math blocks."""
        SEP = re.compile(r'(\\\[.*?\\\]|\\\(.*?\\\))', re.S)
        parts = SEP.split(text)
        result = []
        for part in parts:
            if part.startswith('\\(') or part.startswith('\\['):
                result.append(part)
                continue

            # d²y/dx² style — handle both ASCII d^2 and Unicode ² (U+00B2)
            part = re.sub(
                r'd[\^²]?2?\s*([a-zA-Z])\s*/\s*d([a-zA-Z])[\^²]?2?',
                lambda m: f'\\(\\frac{{d^2{m.group(1)}}}{{d{m.group(2)}^2}}\\)',
                part
            )
            # Also handle Unicode superscript 2: d²y/dx²
            part = re.sub(
                r'd²\s*([a-zA-Z])\s*/\s*d([a-zA-Z])²',
                lambda m: f'\\(\\frac{{d^2{m.group(1)}}}{{d{m.group(2)}^2}}\\)',
                part
            )
            # dy/dx, df/dx, dv/dt etc.
            part = re.sub(
                r'\bd([a-zA-Z])/d([a-zA-Z])\b',
                lambda m: f'\\(\\frac{{d{m.group(1)}}}{{d{m.group(2)}}}\\)',
                part
            )
            # ∂f/∂x (text versions like df/dx for partial)
            part = re.sub(
                r'∂([a-zA-Z]+)/∂([a-zA-Z]+)',
                lambda m: f'\\(\\frac{{\\partial {m.group(1)}}}{{\\partial {m.group(2)}}}\\)',
                part
            )
            result.append(part)
        return ''.join(result)

    def _fix_q_latex(self, q: dict) -> None:
        """Apply LaTeX fixes to all text fields of a question dict."""
        if q.get('text'):
            q['text'] = self._fix_text_latex(q['text'])
        if q.get('options'):
            q['options'] = [self._fix_text_latex(o) for o in q['options']]
        if q.get('subs'):
            for s in q['subs']:
                if s.get('text'):
                    s['text'] = self._fix_text_latex(s['text'])

    def _convert_dollar_math(self, text: str) -> str:
        """Convert $...$ to \\(...\\) only in plain-text segments."""
        # Split on already-delimited math to avoid double-wrapping
        SEP = re.compile(r'(\\\[.*?\\\]|\\\(.*?\\\))', re.S)
        parts = SEP.split(text)
        result = []
        for part in parts:
            if part.startswith('\\(') or part.startswith('\\['):
                result.append(part)
            else:
                # Replace $...$ → \(...\), but not $$ (already handled)
                result.append(re.sub(r'(?<!\$)\$(?!\$)(.+?)(?<!\$)\$(?!\$)',
                                     r'\\(\1\\)', part, flags=re.S))
        return ''.join(result)

    def _convert_unicode_math(self, text: str) -> str:
        """Replace Unicode math chars with LaTeX equivalents.

        If the char appears inside an existing \\(...\\) or \\[...\\] block,
        just replace the symbol. If it's in plain text, wrap the minimal
        expression containing it.
        """
        if not any(c in text for c in self._UNICODE_MAP):
            return text

        SEP = re.compile(r'(\\\[.*?\\\]|\\\(.*?\\\))', re.S)
        parts = SEP.split(text)
        result = []
        for part in parts:
            if part.startswith('\\(') or part.startswith('\\['):
                # Inside math — just replace symbols
                for uc, lat in self._UNICODE_MAP.items():
                    part = part.replace(uc, lat)
                result.append(part)
            else:
                # Outside math — replace symbols and wrap each occurrence
                for uc, lat in self._UNICODE_MAP.items():
                    if uc in part:
                        part = part.replace(uc, f'\\({lat}\\)')
                result.append(part)
        return ''.join(result)

    def _wrap_bare_commands(self, text: str) -> str:
        """Wrap standalone LaTeX commands that are not inside \\(...\\) or \\[...\\].

        Targets common patterns:
          \frac{a}{b}  →  \( \frac{a}{b} \)
          \int_0^n     →  \( \int_0^n \)
          \sum_{i=0}   →  \( \sum_{i=0} \)
          \begin{...}...\end{...}  →  \[ ... \]
        """
        # Split on already-delimited regions
        SEP = re.compile(r'(\\\[.*?\\\]|\\\(.*?\\\))', re.S)
        parts = SEP.split(text)
        result = []
        for part in parts:
            if part.startswith('\\(') or part.startswith('\\['):
                result.append(part)
            else:
                # Wrap \begin{env}...\end{env} as display math
                part = re.sub(
                    r'(\\begin\{[a-z*]+\}.*?\\end\{[a-z*]+\})',
                    r'\\[\1\\]', part, flags=re.S
                )
                # Wrap bare \cmd{...}{...} or \cmd_... expressions
                # Match: \cmd optionally followed by [opt], {arg}, ^, _, spaces
                part = re.sub(
                    r'(?<![\\(\[{^_])'      # not already inside something
                    r'(\\(?:frac|int|iint|iiint|oint|sum|prod|lim|sqrt|vec|hat|bar|dot|ddot|tilde|overline|underline|underbrace|overbrace|text|mathrm|mathbf|mathbb|mathcal|left|right|binom|dfrac|tfrac|cfrac|partial|nabla|infty|alpha|beta|gamma|delta|epsilon|zeta|eta|theta|iota|kappa|lambda|mu|nu|xi|pi|rho|sigma|tau|upsilon|phi|chi|psi|omega|Gamma|Delta|Theta|Lambda|Xi|Pi|Sigma|Phi|Psi|Omega|pm|mp|times|div|cdot|leq|geq|neq|approx|equiv|ll|gg|in|notin|subset|subseteq|cup|cap|forall|exists|wedge|vee|neg|rightarrow|leftarrow|Rightarrow|Leftarrow|leftrightarrow|Leftrightarrow|ldots|cdots|vdots|ddots)'
                    r'(?:[_^{}\[\]\s\\a-zA-Z0-9(),.*+/!=-]+)?)',
                    r'\\(\1\\)', part
                )
                result.append(part)
        return ''.join(result)

    def _repair_delimiters(self, text: str) -> str:
        """Fix common delimiter mistakes:
        - \( text \)  with extra spaces inside → keep as-is (fine)
        - \(text with no closing → close it
        - Double-wrapped \( \( ... \) \) → flatten
        - \) \( with short plain text between → merge
        """
        # Double-wrap: \( \( x \) \)  → \( x \)
        text = re.sub(r'\\\(\s*\\\((.+?)\\\)\s*\\\)', r'\\(\1\\)', text, flags=re.S)
        text = re.sub(r'\\\[\s*\\\[(.+?)\\\]\s*\\\]', r'\\[\1\\]', text, flags=re.S)

        # Merge adjacent inline blocks \( A \) \( B \) → \( A \, B \)
        # only when separated by just whitespace/operators
        text = re.sub(r'\\\)\s*([+\-=×÷·,;]?)\s*\\\(', r' \1 ', text)

        # Unclosed \( at end of string
        opens = text.count('\\(')
        closes = text.count('\\)')
        if opens > closes:
            text = text.rstrip() + ' \\)' * (opens - closes)

        return text

    def _error_result(self, reason: str, page_num: int) -> dict:
        return {
            "raw": reason,
            "error": "VLM API call failed",
            "page": page_num,
            "document_header": {},
            "sections": [],
            "questions": [],
            "media": [],
            "flags": [reason],
        }

    def _build_prompt(self, page_num: int, total_pages: int) -> str:
        return f"""You are a specialist OCR engine for Indian engineering university examination papers (Anna University / VTU / JNTUH / RGPV and similar).

Your task: extract the COMPLETE structure of page {page_num + 1} of {total_pages} and output ONLY valid JSON — no markdown fences, no commentary.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⚠️  LATEX IS MANDATORY — READ THIS BEFORE ANYTHING ELSE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

RULE 1: EVERY mathematical expression, symbol, variable, equation, or formula
        MUST be encoded in LaTeX. No exceptions. No plain-text math.

RULE 2: Inside a JSON string, every backslash needs to be doubled.
        LaTeX  \\frac{{a}}{{b}}  →  in JSON: "\\\\frac{{a}}{{b}}"
        LaTeX  \\int_0^\\infty   →  in JSON: "\\\\int_0^\\\\infty"

RULE 3: INLINE math (within prose): wrap in \\\\( ... \\\\)
        DISPLAY math (standalone equations, matrices): wrap in \\\\[ ... \\\\]

RULE 4: NEVER output: plain fractions like a/b when it means mathematics
        NEVER output: unicode symbols like ∫ ∑ ∂ π α β √ ∞ ≤ ≥ ≠ ≈ ∀ ∃ ∈ ∪ ∩
        NEVER output: superscripts as plain text like x^2 without LaTeX
        ALWAYS convert ALL of these to proper LaTeX.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
LATEX CONVERSION REFERENCE TABLE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
What you see on paper → What to output in the JSON string

FRACTIONS & CALCULUS:
  a/b (as fraction)    → "\\\\( \\\\frac{{a}}{{b}} \\\\)"
  d/dx f(x)            → "\\\\( \\\\frac{{d}}{{dx}} f(x) \\\\)"
  dy/dx                → "\\\\( \\\\frac{{dy}}{{dx}} \\\\)"
  d²y/dx²              → "\\\\( \\\\frac{{d^2y}}{{dx^2}} \\\\)"
  ∂f/∂x                → "\\\\( \\\\frac{{\\\\partial f}}{{\\\\partial x}} \\\\)"
  ∫f(x)dx              → "\\\\( \\\\int f(x)\\\\,dx \\\\)"
  ∫₀^∞ e^(-st) dt      → "\\\\[ \\\\int_0^{{\\\\infty}} e^{{-st}}\\\\,dt \\\\]"
  ∬∬ f dA              → "\\\\( \\\\iint f\\\\,dA \\\\)"
  L{{f(t)}}             → "\\\\( \\\\mathcal{{L}}\\\\{{f(t)\\\\}} \\\\)"
  F(s)                 → "\\\\( F(s) \\\\)"

ALGEBRA & SERIES:
  x²                   → "\\\\( x^2 \\\\)"
  x^n                  → "\\\\( x^n \\\\)"
  √x                   → "\\\\( \\\\sqrt{{x}} \\\\)"
  ⁿ√x                  → "\\\\( \\\\sqrt[n]{{x}} \\\\)"
  ∑ᵢ₌₀ⁿ               → "\\\\( \\\\sum_{{i=0}}^{{n}} \\\\)"
  ∏ᵢ₌₁ⁿ               → "\\\\( \\\\prod_{{i=1}}^{{n}} \\\\)"
  lim x→0              → "\\\\( \\\\lim_{{x \\\\to 0}} \\\\)"
  |x|                  → "\\\\( |x| \\\\)"
  ||v||                → "\\\\( \\\\|v\\\\| \\\\)"
  ∞                    → "\\\\( \\\\infty \\\\)"

GREEK LETTERS:
  α β γ δ ε ζ η θ      → "\\\\( \\\\alpha \\\\)", "\\\\( \\\\beta \\\\)", etc.
  λ μ ν ξ π ρ σ τ      → "\\\\( \\\\lambda \\\\)", "\\\\( \\\\mu \\\\)", etc.
  φ χ ψ ω              → "\\\\( \\\\phi \\\\)", "\\\\( \\\\chi \\\\)", etc.
  Γ Δ Θ Λ Σ Φ Ψ Ω      → "\\\\( \\\\Gamma \\\\)", "\\\\( \\\\Delta \\\\)", etc.

MATRICES (use display mode):
  A 2×2 matrix         → "\\\\[ \\\\begin{{pmatrix}} a & b \\\\\\\\ c & d \\\\end{{pmatrix}} \\\\]"
  Determinant |A|      → "\\\\( \\\\det(A) \\\\)"
  Transpose Aᵀ         → "\\\\( A^T \\\\)"
  Inverse A⁻¹          → "\\\\( A^{{-1}} \\\\)"
  A·B (matrix mult)    → "\\\\( A \\\\cdot B \\\\)"

LOGIC & SET THEORY:
  ∀x P(x)              → "\\\\( \\\\forall x\\\\, P(x) \\\\)"
  ∃x P(x)              → "\\\\( \\\\exists x\\\\, P(x) \\\\)"
  P → Q                → "\\\\( P \\\\rightarrow Q \\\\)"
  P ↔ Q                → "\\\\( P \\\\leftrightarrow Q \\\\)"
  P ∧ Q                → "\\\\( P \\\\wedge Q \\\\)"
  P ∨ Q                → "\\\\( P \\\\vee Q \\\\)"
  ¬P                   → "\\\\( \\\\neg P \\\\)"
  A ∪ B                → "\\\\( A \\\\cup B \\\\)"
  A ∩ B                → "\\\\( A \\\\cap B \\\\)"
  x ∈ A                → "\\\\( x \\\\in A \\\\)"
  A ⊆ B                → "\\\\( A \\\\subseteq B \\\\)"
  ∅                    → "\\\\( \\\\emptyset \\\\)"

PROBABILITY & STATISTICS:
  P(A|B)               → "\\\\( P(A|B) \\\\)"
  P(A∩B)/P(B)          → "\\\\( \\\\frac{{P(A \\\\cap B)}}{{P(B)}} \\\\)"
  μ = E[X]             → "\\\\( \\\\mu = E[X] \\\\)"
  σ²                   → "\\\\( \\\\sigma^2 \\\\)"
  X ~ N(μ, σ²)         → "\\\\( X \\\\sim \\\\mathcal{{N}}(\\\\mu, \\\\sigma^2) \\\\)"

AI/ML:
  h(n) = g(n) + h*(n)  → "\\\\( h(n) = g(n) + h^*(n) \\\\)"
  wᵢⱼ                  → "\\\\( w_{{ij}} \\\\)"
  ŷ                    → "\\\\( \\\\hat{{y}} \\\\)"
  ∂L/∂w                → "\\\\( \\\\frac{{\\\\partial L}}{{\\\\partial w}} \\\\)"
  argmax f(x)          → "\\\\( \\\\arg\\\\max_x f(x) \\\\)"
  sigmoid σ(x)         → "\\\\( \\\\sigma(x) = \\\\frac{{1}}{{1+e^{{-x}}}} \\\\)"

RELATIONS:
  ≤ ≥ ≠ ≈ ≡ ≪ ≫       → "\\\\( \\\\leq \\\\)", "\\\\( \\\\geq \\\\)", etc.
  ± ∓ × ÷ ·           → "\\\\( \\\\pm \\\\)", "\\\\( \\\\mp \\\\)", etc.
  ∝                    → "\\\\( \\\\propto \\\\)"

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CONCRETE EXAMPLES
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

BAD (never output this):
  "Find dy/dx if y = x^2 + sin(x)"
  "Evaluate ∫₀^π sin(x) dx"
  "If λ = 3, find the eigenvector"

GOOD (always output this):
  "Find \\\\( \\\\frac{{dy}}{{dx}} \\\\) if \\\\( y = x^2 + \\\\sin(x) \\\\)"
  "Evaluate \\\\[ \\\\int_0^{{\\\\pi}} \\\\sin(x)\\\\,dx \\\\]"
  "If \\\\( \\\\lambda = 3 \\\\), find the eigenvector"

BAD (matrix):
  "Find the inverse of matrix [[1,2],[3,4]]"

GOOD (matrix):
  "Find the inverse of \\\\[ \\\\begin{{pmatrix}} 1 & 2 \\\\\\\\ 3 & 4 \\\\end{{pmatrix}} \\\\]"

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
OUTPUT FORMAT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Return ONLY this JSON structure (no markdown, no extra text):

{{
  "page": {page_num},
  "document_header": {{
    "degree": "B.E./B.Tech",
    "exam_session": "NOV/DEC 2023",
    "semester": "V",
    "subject_code": "CS3501",
    "subject_name": "Artificial Intelligence",
    "instructions": ["Answer ALL questions", "All questions carry equal marks"],
    "time": "3 Hours",
    "max_marks": "100"
  }},
  "sections": [
    {{"part": "A", "header": "PART - A (10 x 2 = 20 Marks)", "instruction": "Answer ALL Questions", "default_marks": 2}},
    {{"part": "B", "header": "PART - B (5 x 13 = 65 Marks)", "instruction": "Answer ALL Questions", "default_marks": 13}},
    {{"part": "C", "header": "PART - C (1 x 15 = 15 Marks)", "instruction": "Answer any ONE Question", "default_marks": 15}}
  ],
  "questions": [
    {{
      "number": "1",
      "part": "A",
      "marks": 2,
      "text": "Find \\\\( \\\\frac{{dy}}{{dx}} \\\\) if \\\\( y = x^2 + \\\\sin(x) \\\\).",
      "options": [],
      "subs": [],
      "is_continuation": false,
      "continue_next": false,
      "incomplete": false,
      "media_refs": [],
      "bbox": [0.05, 0.10, 0.95, 0.18],
      "conf": 0.95
    }},
    {{
      "number": "11",
      "part": "B",
      "marks": 13,
      "text": "Solve the differential equation:",
      "options": [],
      "subs": [
        {{"label": "(a)", "text": "\\\\[ \\\\frac{{d^2y}}{{dx^2}} - 4\\\\frac{{dy}}{{dx}} + 4y = e^{{2x}} \\\\]", "marks": 7, "is_or_alternative": false}},
        {{"label": "(b)", "text": "Find the Laplace transform \\\\( \\\\mathcal{{L}}\\\\{{f(t)\\\\}} \\\\) where \\\\( f(t) = e^{{-at}} \\\\sin(bt) \\\\).", "marks": 6, "is_or_alternative": false}}
      ],
      "is_continuation": false,
      "continue_next": false,
      "incomplete": false,
      "media_refs": [],
      "bbox": [0.05, 0.45, 0.95, 0.80],
      "conf": 0.92
    }}
  ],
  "media": [],
  "flags": []
}}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
EXTRACTION RULES
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
1.  Extract EVERY question on this page — missing a question is a critical failure.
2.  Part headers "PART - A / B / C" with marks: set default_marks = marks PER question, not total.
3.  MCQ options (A)(B)(C)(D): populate the options array. Descriptive = options: [].
4.  The "BL CO PO" column on the right margin is NOT part of the question — skip it.
5.  OR alternatives: put both in subs[] with is_or_alternative: true on the second one.
6.  Continuation: is_continuation: true if question started on the previous page.
7.  Cut-off: continue_next: true if the question text is truncated (continues next page).
8.  media_refs: list reference words ("figure", "graph", "circuit", "table", "tree") if present.
9.  bbox: [x0, y0, x1, y1] normalized 0–1 for the question's bounding box.
10. conf: your confidence 0–1 in the extraction accuracy.
11. First page only: populate document_header fully from the paper's header section.
12. ALL mathematical content in text, options, and subs MUST use LaTeX as described above."""


llm = LLM()
