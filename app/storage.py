import shutil
from pathlib import Path
from urllib.parse import quote

from .config import settings


class LocalStorage:
    """Local filesystem storage. Keys are relative paths under the data root."""

    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)

    def put_bytes(self, key: str, data: bytes) -> str:
        p = self.root / key
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        return key

    def put_file(self, key: str, src: Path) -> str:
        p = self.root / key
        p.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, p)
        return key

    def path(self, key: str) -> Path:
        p = self.root / key
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    def exists(self, key: str) -> bool:
        return (self.root / key).exists()

    def read_bytes(self, key: str) -> bytes:
        return (self.root / key).read_bytes()

    def url(self, key: str) -> str:
        return "/files/" + quote(key)


storage = LocalStorage(settings.data_path / "files")
