import sys, re
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.db import SessionLocal
from app.models import Question
from app.generation.context import _group_questions

db = SessionLocal()
questions = db.query(Question).all()
by_key = {}
for q in questions:
    by_key.setdefault(q.document.filename, {})[str(q.number)] = q

def set_text(q, text):
    q.text = text

def set_subs(q, subs):
    q.subs = subs

fixes = []

# ---------------- Q11: broken \frac{...}{\sqrt{}} ----------------
q = by_key["resource_2.pdf"].get("4")
if q:
    set_text(q, r'Evaluation of \( \int \int \int dx\,dy\,dz \) is equal to')
    fixes.append("Q11")

# ---------------- Q17: 'then' inside math delimiters ----------------
q = by_key["resource_2.pdf"].get("6")
if q:
    set_text(q, r'If \( \phi = xyz \), then \( \nabla \phi \) is')
    fixes.append("Q17")

# ---------------- Q24: invalid \curl command ----------------
q = by_key["resource_3.pdf"].get("8")
if q:
    opts = q.options or []
    if len(opts) >= 4:
        opts[3] = r'\(\int \int \nabla \times \mathbf{F} \cdot d\mathbf{s}\)'
        q.options = opts
        fixes.append("Q24")

# ---------------- Q25: text is just display math ----------------
q = by_key["resource.pdf"].get("9")
if q:
    set_text(q, r'Find \( L \left[ e^{3t} \right] \)')
    fixes.append("Q25")

# ---------------- Q26: make e^{-at} a proper superscript ----------------
q = by_key["resource_2.pdf"].get("9")
if q:
    set_text(q, r'Find \( L \left[ e^{-at} \right] \)')
    fixes.append("Q26")

# ---------------- Q27: completely broken math ----------------
q = by_key["resource_3.pdf"].get("9")
if q:
    set_text(q, r'Find \( L \left[ \sinh at \right] \) is')
    opts = q.options or []
    if len(opts) >= 4:
        opts[0] = r'\(\frac{a}{s^2 - a^2}\), if \(s > |a|\)'
        opts[1] = r'\(\frac{s}{s^2 - a^2}\), if \(s > |a|\)'
        opts[2] = r'\(\frac{s}{s^2 + a^2}\), if \(s > |a|\)'
        opts[3] = r'\(\frac{a}{s^2 + a^2}\), if \(s > |a|\)'
        q.options = opts
        fixes.append("Q27")

# ---------------- Q32: broken superscript chars in options ----------------
q = by_key["resource_2.pdf"].get("11")
if q:
    set_text(q, r'Find \( L \left[ \frac{1}{(s-1)^2} \right] \)')
    opts = q.options or []
    if len(opts) >= 4:
        opts[0] = r'\(te^{-t}\)'
        opts[1] = r'\(t e^{t}\)'
        opts[2] = r'\(t^2 e^{t}\)'
        opts[3] = r'\(t\)'
        q.options = opts
        fixes.append("Q32")

# ---------------- Q33: proper math in text ----------------
q = by_key["resource_3.pdf"].get("11")
if q:
    set_text(q, r'Find \( L^{-1} \left[ \frac{1}{(s+a)^2} \right] \)')
    fixes.append("Q33")

# ---------------- Q47: options glued into text with no separators ----------------
q = by_key["resource_2.pdf"].get("16")
if q:
    set_text(q, r'The transformation \( \omega = z + c \), where \( c \) is a complex constant, represents')
    fixes.append("Q47")

# ---------------- Q49: option content glued into text ----------------
q = by_key["resource_2.pdf"].get("17")
if q:
    set_text(q, r'The value of \( \int_C \frac{dz}{z-2} \), where \( C \) is the circle \( |z| = 1 \), is')
    fixes.append("Q49")

# ---------------- Q50: truncated text ----------------
q = by_key["resource_2.pdf"].get("18")
if q:
    set_text(q, r'Let \( C_1 : |z-a| = R_1 \) and \( C_2 : |z-a| = R_2 \) be two concentric circles, the annular region is defined as')
    fixes.append("Q50")

# ---------------- Q51: options glued into text ----------------
q = by_key["resource_2.pdf"].get("19")
if q:
    set_text(q, r'If \( f(z) = \frac{\sin z}{z} \), then')
    fixes.append("Q51")

# ---------------- Q52: options glued into text ----------------
q = by_key["resource_2.pdf"].get("20")
if q:
    set_text(q, r'If \( f(z) \) is analytic inside and on \( C \), the value of \( \int_C \frac{f(z)}{z-a} dz \), where \( C \) is the simple closed curve and \( a \) is any point within \( C \), is')
    fixes.append("Q52")

# ---------------- Part B Q2: subs with None labels ----------------
q = by_key["resource_2.pdf"].get("21")
if q:
    subs = q.subs or []
    # fix labels and math
    if len(subs) >= 2:
        subs[0]["label"] = "(a)"
        subs[0]["text"] = r'By changing the order of integration, evaluate \( \int_0^{4a} \int_0^{\sqrt{4a-x^2}} dy\,dx \).'
        subs[1]["label"] = "(b)"
        subs[1]["is_or_alternative"] = True
        subs[1]["text"] = r'Evaluate \( \int_0^a \int_0^{\sqrt{a^2-x^2}} \int_0^{\sqrt{a^2-x^2-y^2}} dz\,dy\,dx \).'
        q.subs = subs
        fixes.append("PartB-Q2")

# ---------------- Part B Q5: missing parts of the OR question ----------------
q = by_key["resource_2.pdf"].get("22")
if q:
    subs = [
        {"label": "a.i.", "text": r'Find the directional derivative of \( \varphi = x^2yz + 4xz^2 + xyz \) at \( (1,2,3) \) in the direction of \( 2\mathbf{i} + \mathbf{j} - \mathbf{k} \).', "marks": 4, "is_or_alternative": False},
        {"label": "a.ii.", "text": r'Find the angle between the surfaces \( z = x^2 + y^2 - 3 \) and \( x^2 + y^2 + z^2 = 9 \) at \( (2,-1,2) \).', "marks": 4, "is_or_alternative": False},
        {"label": "b.", "text": r'Verify Green\'s theorem in a plane for \( \oint_C (3x^2 - 8y^2)\,dx + (4y - 6xy)\,dy \), where \( C \) is the boundary of the region bounded by \( x=0,\ y=0 \) and \( x+y=1 \).', "marks": 8, "is_or_alternative": True},
    ]
    q.text = ""
    q.subs = subs
    q.flags = [f for f in (q.flags or []) if f.get("code") != "EMPTY_SUB"]
    fixes.append("PartB-Q5")

# ---------------- Part B Q7: broken piecewise ----------------
q = by_key["resource_2.pdf"].get("23")
if q:
    subs = q.subs or []
    if subs:
        subs[0]["text"] = r'Find the Laplace transform of the function \( f(t) = \begin{cases} t & 0 < t < a \\ 2a - t & a < t < 2a \end{cases} \), with \( f(t + 2a) = f(t) \).'
        q.subs = subs
        fixes.append("PartB-Q7")

# ---------------- Part B Q8: plain text math -> LaTeX ----------------
q = by_key["resource_3.pdf"].get("23")
if q:
    set_text(q, r'Find \( L^{-1} \left\{ \frac{s^3}{(s^2 + a^2)(s^2 + b^2)} \right\} \) using the convolution theorem.')
    fixes.append("PartB-Q8")

# ---------------- Part B Q10: improper math notation ----------------
q = by_key["resource_2.pdf"].get("25")
if q:
    subs = q.subs or []
    if subs:
        subs[0]["text"] = r'Using Cauchy integral formula, evaluate \( \int_C \frac{z}{z-2} dz \), where \( C \) is the circle \( |z-2| = \frac{3}{2} \).'
        q.subs = subs
        fixes.append("PartB-Q10")

db.commit()
print("applied fixes:", fixes)
db.close()
