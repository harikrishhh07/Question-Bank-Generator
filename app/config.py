from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(BASE_DIR / ".env"), env_file_encoding="utf-8")

    app_name: str = "Studique QBGen"
    data_dir: str = str(BASE_DIR / "data")
    # Resolved to an absolute sqlite:/// URL in the validator below so the
    # server can be started from any working directory.
    database_url: str = ""

    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    openai_strong_model: str = "gpt-4o"

    # Gemini (Google AI Studio) — set GEMINI_API_KEY in .env to use Gemini instead of OpenAI
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.5-flash-lite"
    gemini_strong_model: str = "gemini-3.5-flash"

    # On macOS/Linux Tesseract is usually on PATH; override in .env on Windows:
    #   TESSERACT_PATH=C:/Program Files/Tesseract-OCR/tesseract.exe
    tesseract_path: str = "tesseract"

    watermark_image: str = "assets/studique-logo.png"
    watermark_opacity: float = 0.15
    watermark_position: str = "center"
    watermark_scale: float = 0.4
    watermark_rotation: float = 0

    output_dir: str = str(BASE_DIR / "output")

    @model_validator(mode="after")
    def _resolve_database_url(self) -> "Settings":
        """Build an absolute sqlite URL if none was set in the environment."""
        if not self.database_url:
            db_path = Path(self.data_dir) / "studique.db"
            db_path.parent.mkdir(parents=True, exist_ok=True)
            self.database_url = f"sqlite:///{db_path.resolve()}"
        return self

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
