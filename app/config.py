from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = Field(default="MedBot", alias="APP_NAME")
    app_env: str = Field(default="development", alias="APP_ENV")
    debug: bool = Field(default=False, alias="DEBUG")

    api_host: str = Field(default="0.0.0.0", alias="API_HOST")
    api_port: int = Field(default=8000, alias="API_PORT")

    telegram_bot_token: str = Field(default="", alias="TELEGRAM_BOT_TOKEN")

    database_url: str = Field(
        default="sqlite+aiosqlite:///./data/medbot.db",
        alias="DATABASE_URL",
    )
    reports_dir: Path = Field(default=Path("data/reports"), alias="REPORTS_DIR")

    mail_imap_host: str = Field(default="imap.mail.ru", alias="MAIL_IMAP_HOST")
    mail_imap_port: int = Field(default=993, alias="MAIL_IMAP_PORT")
    mail_username: str = Field(default="", alias="MAIL_USERNAME")
    mail_password: str = Field(default="", alias="MAIL_PASSWORD")
    mail_folder: str = Field(default="INBOX", alias="MAIL_FOLDER")
    mail_poll_interval_seconds: int = Field(
        default=300,
        alias="MAIL_POLL_INTERVAL_SECONDS",
    )
    mail_allowed_senders: str = Field(default="", alias="MAIL_ALLOWED_SENDERS")
    mail_allowed_subject_fragment: str = Field(
        default="",
        alias="MAIL_ALLOWED_SUBJECT_FRAGMENT",
    )

    gigachat_base_url: str = Field(
        default="https://gigachat.devices.sberbank.ru/api/v1",
        alias="GIGACHAT_BASE_URL",
    )
    gigachat_auth_url: str = Field(
        default="https://ngw.devices.sberbank.ru:9443/api/v2/oauth",
        alias="GIGACHAT_AUTH_URL",
    )
    gigachat_client_id: str = Field(default="", alias="GIGACHAT_CLIENT_ID")
    gigachat_authorization_key: str = Field(
        default="",
        alias="GIGACHAT_AUTHORIZATION_KEY",
    )
    gigachat_scope: str = Field(default="GIGACHAT_API_PERS", alias="GIGACHAT_SCOPE")
    gigachat_model: str = Field(default="GigaChat", alias="GIGACHAT_MODEL")
    gigachat_timeout_seconds: float = Field(
        default=30.0,
        alias="GIGACHAT_TIMEOUT_SECONDS",
    )

    @property
    def mail_allowed_senders_list(self) -> list[str]:
        return [
            sender.strip().lower()
            for sender in self.mail_allowed_senders.split(",")
            if sender.strip()
        ]

    @property
    def mail_allowed_subject_fragment_normalized(self) -> str:
        return " ".join(self.mail_allowed_subject_fragment.lower().replace("ё", "е").split())


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    settings.reports_dir.mkdir(parents=True, exist_ok=True)
    return settings
