from pathlib import Path

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="FCM_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    api_key: SecretStr
    storage_dir: Path = Path(".runtime")
    max_upload_mb: int = Field(default=64, ge=1, le=512)
    max_active_jobs: int = Field(default=4, ge=1, le=16)
    job_budget_sec: float = Field(default=270, gt=0, le=290)
    analyzer_factory: str = ""

    @field_validator("api_key")
    @classmethod
    def require_secret(cls, value: SecretStr) -> SecretStr:
        key = value.get_secret_value()
        if len(key) < 24 or key.startswith("replace-with-"):
            raise ValueError("Run python scripts/init_env.py to generate a local API key")
        return value
