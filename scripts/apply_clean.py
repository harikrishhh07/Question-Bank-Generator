import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.db import SessionLocal
from app.models import Question
from app.pipeline import postprocess

db = SessionLocal()
fixed = 0
for q in db.query(Question).all():
    qd = {
        "number": q.number,
        "part": q.part,
        "text": q.text or "",
        "options": list(q.options or []),
        "subs": list(q.subs or []),
        "flags": list(q.flags or []),
    }
    before_opts = len(qd["options"])
    postprocess.clean_options([qd])
    postprocess.repair_math_delimiters([qd])
    changed = False
    if len(qd["options"]) != before_opts or qd["text"] != (q.text or ""):
        changed = True
    q.text = qd["text"]
    q.options = qd["options"]
    q.subs = qd["subs"]
    if len(qd["flags"]) > len(q.flags or []):
        q.flags = qd["flags"]
    if changed:
        fixed += 1
db.commit()

# report
for q in db.query(Question).all():
    opts = q.options or []
    if opts:
        ph = sum(1 for o in opts if str(o).strip() in {"", "...", ".", ".."})
        print(f"q {q.document.filename} [{q.part}] {q.number}: {len(opts)} opts, {ph} empty/placeholder, flags={[f.get('code') for f in (q.flags or [])]}")
print("fixed rows:", fixed)
db.close()
