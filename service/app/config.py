"""Environment-driven settings."""

from __future__ import annotations

import logging
import math
import os
from dataclasses import dataclass, field
from pathlib import Path

from app.health import vlm_health_url_from_env

log = logging.getLogger(__name__)

DEFAULT_API_KEY = "forte-datathon-2026"
DEFAULT_ANALYZER = "app.analyzers.stub:StubAnalyzer"
DEFAULT_JOB_TIMEOUT_SEC = 270.0
DEFAULT_JOB_HARD_CAP_SEC = 280.0  # absolute ceiling from submission (jury limit: 300 s)
MAX_UPLOAD_BYTES = 200 * 1024 * 1024
DEFAULT_UI_SESSION_HOURS = 12.0
DEFAULT_TRUSTED_PROXIES = "127.0.0.1/32,::1/128"


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError:
        log.warning("Invalid %s=%r, using default %s", name, raw, default)
        return default


def _env_positive_sec(name: str, default: float) -> float:
    value = _env_float(name, default)
    if not math.isfinite(value) or value <= 0:
        log.warning("Invalid %s=%r (must be a positive finite number of seconds), using default %s",
                    name, os.environ.get(name), default)
        return default
    return value


def _env_max_upload_bytes() -> int:
    mb = _env_float("MAX_UPLOAD_MB", MAX_UPLOAD_BYTES / (1024 * 1024))
    if mb <= 0:
        log.warning("Invalid MAX_UPLOAD_MB=%r, using default", mb)
        return MAX_UPLOAD_BYTES
    return int(mb * 1024 * 1024)


@dataclass
class Settings:
    api_key: str = DEFAULT_API_KEY
    api_key_is_default: bool = True
    data_dir: Path = field(default_factory=lambda: Path("./data"))
    job_timeout_sec: float = DEFAULT_JOB_TIMEOUT_SEC
    job_hard_cap_sec: float = DEFAULT_JOB_HARD_CAP_SEC
    analyzer: str = DEFAULT_ANALYZER
    stub_debug: bool = False
    max_upload_bytes: int = MAX_UPLOAD_BYTES
    public_url_file: Path | None = None  # None -> data_dir / "public_url.txt"
    public_hostname: str = ""  # PUBLIC_HOSTNAME (Caddy / Tailscale Funnel); wins over public_url_file
    rules_path: str = ""  # RULES_PATH; empty -> default lookup order (see app.rules)
    # VLM /health URL probed by /healthz; None -> not probed (vlm_ok: null). from_env derives it
    # from VLM_HEALTH_URL / FORTE_VLM_URL (see app.health).
    vlm_health_url: str | None = None
    # Browser UI gate (app.ui_auth). No UI_PASSWORD and no UI_OPEN -> UI answers 503; /api/v1 unaffected.
    ui_password: str = ""
    ui_open: bool = False  # UI_OPEN=1: no gate (dev/tests only)
    ui_secret: str = ""  # cookie signing key; empty -> DATA_DIR/ui_secret (generated, 0600)
    ui_session_hours: float = DEFAULT_UI_SESSION_HOURS
    trusted_proxies: str = DEFAULT_TRUSTED_PROXIES  # CIDRs whose X-Forwarded-For/-Proto are trusted

    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def clips_dir(self) -> Path:
        return self.data_dir / "clips"

    @property
    def jobs_dir(self) -> Path:
        return self.data_dir / "jobs"

    @property
    def public_url_path(self) -> Path:
        """File with the current public tunnel URL (written by scripts/public_url.sh)."""
        return self.public_url_file or self.data_dir / "public_url.txt"

    @classmethod
    def from_env(cls) -> "Settings":
        api_key = os.environ.get("API_KEY") or ""
        is_default = not api_key
        if is_default:
            api_key = DEFAULT_API_KEY
            log.warning("API_KEY is not set; using the default key %r", DEFAULT_API_KEY)
        job_timeout = _env_positive_sec("JOB_TIMEOUT_SEC", DEFAULT_JOB_TIMEOUT_SEC)
        hard_cap = _env_positive_sec("JOB_HARD_CAP_SEC", DEFAULT_JOB_HARD_CAP_SEC)
        if job_timeout > hard_cap:
            log.warning("JOB_TIMEOUT_SEC=%g exceeds JOB_HARD_CAP_SEC=%g; the hard cap wins "
                        "(jobs end at most %g s after submit)", job_timeout, hard_cap, hard_cap)
        return cls(
            api_key=api_key,
            api_key_is_default=is_default,
            data_dir=Path(os.environ.get("DATA_DIR") or "./data"),
            job_timeout_sec=job_timeout,
            job_hard_cap_sec=hard_cap,
            analyzer=os.environ.get("ANALYZER") or DEFAULT_ANALYZER,
            stub_debug=_env_bool("STUB_DEBUG"),
            max_upload_bytes=_env_max_upload_bytes(),
            public_url_file=Path(os.environ["PUBLIC_URL_FILE"]) if os.environ.get("PUBLIC_URL_FILE") else None,
            public_hostname=(os.environ.get("PUBLIC_HOSTNAME") or "").strip(),
            rules_path=(os.environ.get("RULES_PATH") or "").strip(),
            vlm_health_url=vlm_health_url_from_env(),
            ui_password=os.environ.get("UI_PASSWORD") or "",
            ui_open=_env_bool("UI_OPEN"),
            ui_secret=os.environ.get("UI_SECRET") or "",
            ui_session_hours=_env_positive_sec("UI_SESSION_HOURS", DEFAULT_UI_SESSION_HOURS),
            trusted_proxies=(os.environ.get("TRUSTED_PROXIES") or DEFAULT_TRUSTED_PROXIES),
        )


def get_settings() -> Settings:
    return Settings.from_env()
