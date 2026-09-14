from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy.orm import Session

from ..config import BASE_DIR, settings
from ..models import Collection, QbGeneration
from ..storage import storage
from .context import build_context

TEMPLATES_DIR = Path(__file__).parent / "templates"
KATEX_DIR = TEMPLATES_DIR / "katex"

_env = Environment(
    loader=FileSystemLoader(str(TEMPLATES_DIR)),
    autoescape=select_autoescape(["html"]),
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
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(tmp_html.resolve().as_uri(), wait_until="networkidle")
        if "tex-chtml.js" in html:
            # MathJax typesets automatically after load; give it time (async, ~3-5s for large docs)
            page.wait_for_timeout(6000)
        else:
            page.evaluate(
                """() => {
                    if (typeof renderMathInElement === 'function') {
                        renderMathInElement(document.body, {
                            delimiters: [
                                {left: '\\\\\\(', right: '\\\\\\)', display: false},
                                {left: '\\\\\\\\(', right: '\\\\\\\\\\)', display: false},
                                {left: '\\\\[', right: '\\\\]', display: true},
                                {left: '\\\\\\[', right: '\\\\\\\\]', display: true},
                            ],
                            throwOnError: false
                        });
                    }
                }"""
            )
            page.wait_for_timeout(500)
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


def _header_html(logo_url: str | None, subject: str) -> str:
    logo = logo_url or ""
    return f"""<div style="width:100%;height:10mm;padding:0 16mm;display:flex;align-items:center;justify-content:space-between;
        border-bottom:0.4mm solid #fe6e00;font-family:'Segoe UI',Arial,sans-serif;background:#ffffff;
        font-size:9pt;color:#555;">
        <div style="display:flex;align-items:center;gap:3mm;">
            <img src="{logo}" style="height:5mm;width:5mm;object-fit:contain;" onerror="this.style.display='none'">
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
