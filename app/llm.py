import base64
from pathlib import Path

from openai import OpenAI

from .config import settings


class LLM:
    """Vision-language model interface for document understanding."""

    def __init__(self):
        self._client: OpenAI | None = None
        if settings.openai_api_key:
            self._client = OpenAI(api_key=settings.openai_api_key)

    @property
    def available(self) -> bool:
        return self._client is not None

    def _encode_image(self, image_path: Path) -> str:
        return base64.b64encode(image_path.read_bytes()).decode("utf-8")

    def understand_page(self, image_path: Path, page_num: int = 0, total_pages: int = 1,
                        strong: bool = False) -> dict | None:
        """Send a page image to the VLM and return structured JSON."""
        if not self._client:
            return None

        model = settings.openai_strong_model if strong else settings.openai_model
        b64 = self._encode_image(image_path)

        prompt = self._build_prompt(page_num, total_pages)

        response = self._client.responses.create(
            model=model,
            input=[
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": prompt},
                        {"type": "input_image", "image_url": f"data:image/png;base64,{b64}", "detail": "high"},
                    ],
                }
            ],
            text={"format": {"type": "json_object"}},
        )

        text = response.output_text
        import json

        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"raw": text, "error": "JSON parse failed"}

    def _build_prompt(self, page_num: int, total_pages: int) -> str:
        return f"""You are an expert document analyst for engineering question papers. 
Analyze this page image (page {page_num + 1} of {total_pages}) of a scanned examination paper and extract its complete structure as JSON.

Return ONLY valid JSON with this exact schema:

{{
  "page": {page_num},
  "document_header": {{
    "degree": "",
    "exam_session": "",
    "semester": "",
    "subject_code": "",
    "subject_name": "",
    "instructions": [],
    "time": "",
    "max_marks": ""
  }},
  "sections": [
    {{"part": "A", "header": "PART - A (20 x 1 = 20 Marks)", "instruction": "Answer ALL Questions", "default_marks": 1, "question_span": "20 x 1 = 20 Marks"}}
  ],
  "questions": [
    {{
      "number": "",
      "part": "A",
      "marks": null,
      "text": "",
      "options": ["(A) ...", "(B) ...", "(C) ...", "(D) ..."],
      "subs": [],
      "is_continuation": false,
      "continue_next": false,
      "incomplete": false,
      "media_refs": [],
      "bbox": [0.0, 0.0, 1.0, 1.0],
      "conf": 0.95
    }}
  ],
  "media": [
    {{"id": "m1", "type": "figure|table|equation|image", "bbox": [0.0, 0.0, 1.0, 1.0], "caption": "", "near_questions": ["1"], "conf": 0.9}}
  ],
  "flags": []
}}

CRITICAL:
- The `header` field in sections MUST contain the EXACT section header line text including the marks span, like "PART - A (20 x 1 = 20 Marks)". Do NOT leave it empty.
- `default_marks` is the marks PER QUESTION in this section (e.g. for "20 x 1 = 20 Marks", default_marks = 1, NOT 20).
- Extract ALL text exactly as printed. Preserve math notation as best as possible.
- For MCQ questions, extract all 4 options as the `options` array. ALWAYS extract the full text of EVERY option — never leave an option empty, and never invent options the question does not have.
- IMPORTANT: Only include `options` if the question genuinely shows (A), (B), (C), (D) answer choices. Descriptive/long-answer questions (typically PART B and C) must have `options: []`.
- The "Marks BL CO PO" (or BL CO FO) weightage table on the right/bottom of the page is NOT part of the questions. Never treat its column letters, numbers, or dots as answer options.
- For subquestions like (a),(b) with OR between them, add to `subs` array with `"is_or_alternative": true` for alternatives.
- IMPORTANT: When parts are separated by "(OR)", each alternative is a full question. Extract the COMPLETE text of EVERY alternative — never leave an alternative's text empty, even if it is long or continues on the next line.
- Normalize bbox coordinates 0-1 for each question and media region.
- Set `is_continuation: true` if this question is a continuation from the previous page.
- Set `continue_next: true` if the question visibly continues onto the next page.
- Set `incomplete: true` if the question text is cut off at the page bottom.
- `media_refs` should list any words in the question text that reference figures/diagrams (e.g. "figure", "diagram", "graph", "circuit", "table").
- If the page has a diagram, table, or equation as a visual element, add it to `media` with its bbox.
- `near_questions` lists question numbers that are visually near or associated with the media.
- Be thorough — extract every question on the page.
- If this is the first page, extract the document header."""

    def understand_page_batch(self, page_paths: list[Path], strong: bool = False) -> list[dict]:
        """Process multiple pages, returning list of results."""
        results = []
        for i, path in enumerate(page_paths):
            result = self.understand_page(path, i, len(page_paths), strong)
            results.append(result or {})
        return results


llm = LLM()