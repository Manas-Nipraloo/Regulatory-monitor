from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8-sig")

    app_name: str = "Regulatory Monitor"
    app_env: str = "local"
    data_root: Path = Field(default=Path("./data"))
    download_timeout_seconds: int = 60
    summary_max_words: int = 180
    google_drive_root_folder_url: str = (
        "https://drive.google.com/drive/folders/15sBC2D-zFsQrN6f0oOXW0pvK0SUvlKLi"
    )
    google_sheet_url: str = ""
    google_service_account_file: str = ""
    google_oauth_credentials_file: Path = Path("config/credentials/google_oauth_credentials.json")
    sheet_name: str = ""
    email_subject: str = "Kindly find the New Circulars and updated Drive link"
    email_from: str = "manas.a@nipralo.com"
    email_to: str = ""
    email_cc: str = ""
    email_signature_name: str = "Manas Aher"
    email_signature_title: str = "Junior Web Developer"
    groq_api_key: str = ""
    groq_model: str = "llama-3.3-70b-versatile"
    groq_max_pdf_pages: int = 5
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.5-flash"
    email_credentials_file: Path = Path("config/credentials/email_draft_credentials.json")
    supabase_url: str = ""
    supabase_key: str = ""
    pending_lookback_days: int = 3


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.data_root.mkdir(parents=True, exist_ok=True)
    return settings
