import os
from pathlib import Path
from typing import Dict, List, Optional

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings


class AppConfig(BaseModel):
    log_level: str = "INFO"
    temp_dir: str = "/tmp/cc-statements"
    db_path: str = "./data/statements.db"


class GmailConfig(BaseModel):
    credentials_file: str = "credentials.json"
    token_file: str = "token.json"
    pubsub_topic: str = ""
    pubsub_subscription: str = ""
    label_filter: str = "INBOX"


class UserConfig(BaseModel):
    id_number: str = ""
    birthday: str = ""
    phone: str = ""


class FireflyConfig(BaseModel):
    base_url: str = ""
    api_token: str = ""
    timeout: int = 30
    max_retries: int = 3


class CardConfig(BaseModel):
    cards: Dict[str, str] = Field(default_factory=dict)


class BankConfig(BaseModel):
    name: str
    sender_patterns: List[str] = Field(default_factory=list)
    subject_keywords: List[str] = Field(default_factory=list)
    pdf_password_template: str = ""
    parser_class: str = ""
    cards: Dict[str, str] = Field(default_factory=dict)


class Settings(BaseModel):
    app: AppConfig = AppConfig()
    gmail: GmailConfig = GmailConfig()
    user: UserConfig = UserConfig()
    firefly: FireflyConfig = FireflyConfig()
    banks: Dict[str, BankConfig] = Field(default_factory=dict)


_settings: Optional[Settings] = None


def load_config(config_path: str = "config/config.yaml") -> Settings:
    global _settings
    path = Path(config_path)
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        # Allow env var overrides
        if os.environ.get("FIREFLY_API_TOKEN"):
            data.setdefault("firefly", {})["api_token"] = os.environ["FIREFLY_API_TOKEN"]
        if os.environ.get("FIREFLY_BASE_URL"):
            data.setdefault("firefly", {})["base_url"] = os.environ["FIREFLY_BASE_URL"]
        _settings = Settings(**data)
    else:
        _settings = Settings()
    # Ensure temp dir exists
    Path(_settings.app.temp_dir).mkdir(parents=True, exist_ok=True)
    Path(_settings.app.db_path).parent.mkdir(parents=True, exist_ok=True)
    return _settings


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        return load_config()
    return _settings
