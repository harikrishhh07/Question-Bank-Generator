import sys
sys.path.insert(0, r"C:\Users\1406u\OneDrive\Desktop\Utkarsh\Workstation\qbgen")
from app.db import SessionLocal
from app.models import Collection
from app.generation import document

db = SessionLocal()
col = db.query(Collection).first()
config = {
    "bank_title": "QUESTION BANK",
    "course": "B.Tech / M.Tech (Integrated)",
    "semester": "Second Semester",
    "coverage": "All Units",
    "group_by": "part",
    "notes": "Compiled from previous year question papers.",
}
result = document.generate_bank(db, col.id, config)
print("GEN:", result)
db.close()
