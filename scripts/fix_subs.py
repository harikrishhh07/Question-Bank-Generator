import sys
sys.path.insert(0, r"C:\Users\1406u\OneDrive\Desktop\Utkarsh\Workstation\qbgen")
from app.db import SessionLocal
from app.models import Question

db = SessionLocal()

def q(doc_id, num):
    return db.query(Question).filter(Question.document_id == doc_id, Question.number == num).first()

fixes = []

# Part B Q2 (resource_2 id=2, number 21): clear text, fix subs with fresh list
qq = q(2, "21")
if qq:
    qq.text = ""
    qq.subs = [
        {"label": "(a)", "text": r'By changing the order of integration, evaluate \( \int_0^{4a} \int_0^{\sqrt{4a-x^2}} dy\,dx \).', "marks": 4, "is_or_alternative": False},
        {"label": "(b)", "text": r'Evaluate \( \int_0^a \int_0^{\sqrt{a^2-x^2}} \int_0^{\sqrt{a^2-x^2-y^2}} dz\,dy\,dx \).', "marks": 4, "is_or_alternative": True},
    ]
    fixes.append("PartB-Q2")

# Part B Q7 (resource_2 23): piecewise function
qq = q(2, "23")
if qq:
    qq.subs = [
        {"label": "a.", "text": r'Find the Laplace transform of the function \( f(t) = \begin{cases} t & 0 < t < a \\ 2a - t & a < t < 2a \end{cases} \), where \( f(t + 2a) = f(t) \).', "marks": 8, "is_or_alternative": False},
    ]
    fixes.append("PartB-Q7")

# Part B Q10 (resource_2 25): proper math
qq = q(2, "25")
if qq:
    qq.subs = [
        {"label": "i.", "text": r'Using the Cauchy integral formula, evaluate \( \int_C \frac{z}{z-2}\,dz \), where \( C \) is the circle \( |z-2| = \frac{3}{2} \).', "marks": 8, "is_or_alternative": False},
    ]
    fixes.append("PartB-Q10")

# Part B Q5 (resource_2 22): remove stray backslash in Green's
qq = q(2, "22")
if qq:
    for s in (qq.subs or []):
        if "Green\\'s" in (s.get("text") or ""):
            s["text"] = (s.get("text") or "").replace("Green\\'s", "Green's")
        s["text"] = (s.get("text") or "").replace("\\'", "'")
    fixes.append("PartB-Q5-cleanup")

db.commit()
print("applied:", fixes)
db.close()
