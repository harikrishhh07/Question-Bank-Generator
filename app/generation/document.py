from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader
from sqlalchemy.orm import Session

from ..config import BASE_DIR, settings
from ..models import Collection, QbGeneration
from ..storage import storage
from .context import build_context

TEMPLATES_DIR = Path(__file__).parent / "templates"
KATEX_DIR = TEMPLATES_DIR / "katex"

_env = Environment(
    loader=FileSystemLoader(str(TEMPLATES_DIR)),
    autoescape=False,  # Math LaTeX (backslashes, braces) must not be HTML-escaped
)


def _resolve_watermark(config: dict) -> tuple[str | None, dict]:
    wm = config.get("watermark") or {}
    image = wm.get("image") or settings.watermark_image or ""
    if image:
        p = Path(image)
        if not p.is_absolute():
            p = BASE_DIR / p
        if p.exists():
            return p.resolve().as_uri(), wm
    return None, wm


def render_html(context: dict, template_name: str = "standard/template.html.j2") -> str:
    css_path = TEMPLATES_DIR / "standard" / "styles.css"
    css = css_path.read_text(encoding="utf-8")
    template = _env.get_template(template_name)
    context["_katex_url"] = KATEX_DIR.resolve().as_uri()
    return template.render(css=css, watermark_img=context.pop("_watermark_img", None), **context)


def generate_pdf(html: str, output_path: Path, logo_url: str | None = None, subject: str = "") -> None:
    from playwright.sync_api import sync_playwright

    # write HTML into the katex dir so relative asset paths resolve to file:// URLs
    tmp_html = KATEX_DIR / "_render.html"
    tmp_html.write_text(html, encoding="utf-8")

    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--single-process", "--no-sandbox"])
        page = browser.new_page()
        page.goto(tmp_html.resolve().as_uri(), wait_until="networkidle")
        if "tex-chtml.js" in html:
            # MathJax typesets automatically after load; give it time (async, ~3-5s for large docs)
            page.wait_for_timeout(6000)
        else:
            # KaTeX auto-render: call renderMathInElement with correct delimiters.
            # Using page.evaluate with a JS function avoids Python escape-layer issues.
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
            margin={"top": "18mm", "bottom": "16mm", "left": "16mm", "right": "16mm"},
            display_header_footer=True,
            header_template=_header_html(logo_url, subject),
            footer_template="""
                <div style="width:100%;font-size:8pt;color:#888;text-align:center;
                    padding:2mm 16mm;font-family:'Segoe UI',Arial,sans-serif;">
                    STUDIQUE &middot; Question Bank &nbsp;&nbsp;|&nbsp;&nbsp; Page
                    <span class="pageNumber"></span> of <span class="totalPages"></span>
                </div>
            """,
        )
        browser.close()
        tmp_html.unlink(missing_ok=True)


def _logo_data_uri(logo_url: str | None) -> str:
    """Convert a file:// logo URL to a base64 data URI.

    Playwright's header/footer template runs in an isolated print context that
    cannot load file:// URIs, so we embed the image inline as a data URI.
    """
    if not logo_url:
        return ""
    try:
        import base64
        from urllib.request import urlopen
        from urllib.parse import urlparse
        parsed = urlparse(logo_url)
        if parsed.scheme == "file":
            # Convert file:// URI → local path
            from urllib.request import url2pathname
            local_path = Path(url2pathname(parsed.path))
        else:
            return logo_url  # non-file URL, use as-is and hope for the best
        if not local_path.exists():
            return ""
        data = base64.b64encode(local_path.read_bytes()).decode("ascii")
        suffix = local_path.suffix.lower().lstrip(".")
        mime = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
                "gif": "image/gif", "svg": "image/svg+xml"}.get(suffix, "image/png")
        return f"data:{mime};base64,{data}"
    except Exception:
        return ""


def _header_html(logo_url: str | None, subject: str) -> str:
    # Embed logo as base64 — Playwright print headers cannot load file:// URIs
    logo_data = _logo_data_uri(logo_url)
    logo_img = (
        f'<img src="{logo_data}" style="height:6mm;width:auto;object-fit:contain;display:block;">'
        if logo_data else ""
    )
    return f"""<div style="width:100%;height:10mm;padding:0 16mm;display:flex;align-items:center;justify-content:space-between;
        border-bottom:0.4mm solid #fe6e00;font-family:'Segoe UI',Arial,sans-serif;background:#ffffff;
        font-size:9pt;color:#555;">
        <div style="display:flex;align-items:center;gap:3mm;">
            {logo_img}
            <span style="font-weight:800;letter-spacing:0.08em;color:#fe6e00;font-size:9.5pt;">STUDIQUE</span>
        </div>
        <div style="font-weight:600;text-align:right;font-size:8pt;">{subject}</div>
    </div>"""


def generate_bank(db: Session, collection_id: int, config: dict) -> dict:
    """Generate a Question Bank PDF for a collection. Returns generation info."""
    collection = db.query(Collection).filter(Collection.id == collection_id).first()
    if not collection:
        raise ValueError(f"collection {collection_id} not found")

    context = build_context(db, collection, config)

    # watermark resolution
    wm_img, wm_config = _resolve_watermark(config)
    context["_watermark_img"] = wm_img

    html = render_html(context, config.get("template", "standard/template.html.j2"))
    from datetime import datetime, timezone

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    name = config.get("bank_title") or "Question_Bank"
    out_key = f"banks/bank_{collection_id}_{stamp}.pdf"
    out_path = storage.path(out_key)

    # fix @page margin conflict: ensure CSS does not fight the pdf() margins
    html = html.replace("@page {", "@page { size: A4;", 1)

    subject = context.get("meta", {}).get("subject_name", "") or ""
    generate_pdf(html, out_path, logo_url=context.get("logo_url"), subject=subject)

    gen = QbGeneration(
        collection_id=collection_id,
        name=name,
        template=config.get("template", "standard"),
        config=config,
        output_key=out_key,
        question_count=context["stats"]["total_questions"],
    )
    db.add(gen)
    db.commit()

    return {"generation_id": gen.id, "output_key": out_key, "name": name, "question_count": gen.question_count}
