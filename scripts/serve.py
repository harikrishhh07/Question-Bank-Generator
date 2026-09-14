import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import uvicorn

from app.config import settings

if __name__ == "__main__":
    uvicorn.run("app.web.main:app", host="127.0.0.1", port=8000, reload=False)
