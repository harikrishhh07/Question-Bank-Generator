from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(BASE_DIR / ".env"), env_file_encoding="utf-8")

    app_name: str = "Studique QBGen"
    data_dir: str = str(BASE_DIR / "data")
    database_url: str = "sqlite:///./data/studique.db"

    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    openai_strong_model: str = "gpt-4o"

    tesseract_path: str = "C:/Program Files/Tesseract-OCR/tesseract.exe"

    watermark_image: str = "assets/studique-logo.png"
    watermark_opacity: float = 0.15
    watermark_position: str = "center"
    watermark_scale: float = 0.4
    watermark_rotation: float = 0

    output_dir: str = str(BASE_DIR / "output")

    @property
    def data_path(self) -> Path:
        return Path(self.data_dir)

    @property
    def output_path(self) -> Path:
        return Path(self.output_dir)

    @property
    def render_path(self) -> Path:
        p = self.data_path / "renders"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def media_path(self) -> Path:
        p = self.data_path / "media"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def banks_path(self) -> Path:
        p = self.data_path / "banks"
        p.mkdir(parents=True, exist_ok=True)
        return p


settings = Settings()
