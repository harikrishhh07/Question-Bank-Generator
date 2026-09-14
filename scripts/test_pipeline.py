import sys
sys.path.insert(0, r"C:\Users\1406u\OneDrive\Desktop\Utkarsh\Workstation\qbgen")
from pathlib import Path

from app.db import SessionLocal, init_db
from app.models import Collection, Document
from app.storage import storage
from app.pipeline import pipeline

init_db()
db = SessionLocal()

for col in db.query(Collection).all():
    db.delete(col)
db.commit()

base = Path(r"C:\Users\1406u\OneDrive\Desktop\sample pyqp")
col = Collection(name="ACCA PYQs")
db.add(col)
db.flush()

for fname in ["resource.pdf", "resource_2.pdf", "resource_3.pdf"]:
    src = base / fname
    key = storage.put_file(f"source/{fname}", src)
    doc = Document(collection_id=col.id, filename=fname, storage_key=key)
    db.add(doc)
    db.commit()
    result = pipeline.process_document(db, doc, src)
    print(f"{fname}: {result['status']} questions={result['questions']} media={result['media']} pages={result['pages']}")

db.close()
