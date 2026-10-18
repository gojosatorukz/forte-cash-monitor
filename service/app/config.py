"""Environment-driven settings."""

from __future__ import annotations

import logging
import math
import os
from dataclasses import dataclass, field
from pathlib import Path

from app.health import vlm_health_url_from_env

log = logging.getLogger(__name__)

# Local-development key, used ONLY when API_KEY_DEV_DEFAULT=1 and no valid API_KEY is set. It is public
# (it is in the source), so it must never be enabled on a reachable deployment.
DEV_API_KEY = "forte-datathon-2026"
MIN_API_KEY_LEN = 16
API_KEY_NOT_CONFIGURED = "API_KEY не задан или слишком короткий (минимум 16 символов)"
DEFAULT_ANALYZER = "app.analyzers.stub:StubAnalyzer"
DEFAULT_JOB_TIMEOUT_SEC = 270.0
DEFAULT_JOB_HARD_CAP_SEC = 280.0  # absolute ceiling from submission (jury limit: 300 s)
MAX_UPLOAD_BYTES = 200 * 1024 * 1024
DEFAULT_UI_SESSION_HOURS = 12.0
DEFAULT_TRUSTED_PROXIES = "127.0.0.1/32,::1/128"
DEFAULT_JOB_WORKERS = 1
MAX_JOB_WORKERS = 8
DEFAULT_CLIP_MAX_PARALLEL = 4
DEFAULT_ANALYZER_LOAD_TIMEOUT_SEC = 240.0
MAX_CLIP_MAX_PARALLEL = 16


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


def _env_int_clamped(name: str, default: int, lo: int, hi: int) -> int:
    """Integer env var clamped to lo..hi; unset/blank -> default, non-integer -> default (warned)."""
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw.strip())
    except ValueError:
        log.warning("Invalid %s=%r (must be an integer), using default %s", name, raw, default)
        return default
    clamped = clamp_int(value, lo, hi)
    if clamped != value:
        log.warning("%s=%d is outside %d..%d, using %d", name, value, lo, hi, clamped)
    return clamped


def clamp_int(value: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, int(value)))


BRANDS = ("forte", "neutral")
DEFAULT_BRAND = "forte"


def _env_brand() -> str:
    """BRAND: forte (official Forte logo, default) | neutral (Cash Monitor wordmark, no Forte artwork)."""
    raw = (os.environ.get("BRAND") or "").strip().lower()
    if not raw:
        return DEFAULT_BRAND
    if raw not in BRANDS:
        log.warning("Invalid BRAND=%r (expected forte|neutral), using %s", os.environ.get("BRAND"), DEFAULT_BRAND)
        return DEFAULT_BRAND
    return raw


def _env_live_replay() -> bool:
    """LIVE_REPLAY: live replay of the operation steps in the UI player; on unless 0/false/no/off."""
    raw = (os.environ.get("LIVE_REPLAY") or "").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def _env_max_upload_bytes() -> int:
    mb = _env_float("MAX_UPLOAD_MB", MAX_UPLOAD_BYTES / (1024 * 1024))
    if mb <= 0:
        log.warning("Invalid MAX_UPLOAD_MB=%r, using default", mb)
        return MAX_UPLOAD_BYTES
    return int(mb * 1024 * 1024)


@dataclass
class Settings:
    # No default key: empty / shorter than MIN_API_KEY_LEN -> /api/v1/* answers 503 (see api_key_configured).
    api_key: str = ""
    api_key_is_dev_default: bool = False  # True only when API_KEY_DEV_DEFAULT=1 supplied DEV_API_KEY
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
    brand: str = DEFAULT_BRAND  # BRAND: forte | neutral (UI logo/favicon, see app.branding)
    # LIVE_REPLAY: stage strip / current-step highlight / overlays while the video plays (UI only);
    # injected as <html data-live="1|0"> by app.branding.
    live_replay: bool = True
    # JOB_WORKERS: parallel job threads, each with its own analyzer instance; the detector shares models/VLM client across instances (clamped 1..8, see app.worker).
    job_workers: int = DEFAULT_JOB_WORKERS
    # CLIP_MAX_PARALLEL: process-wide cap on concurrent clip ffmpeg runs across all workers (1..16).
    clip_max_parallel: int = DEFAULT_CLIP_MAX_PARALLEL
    # ANALYZER_LOAD_TIMEOUT_SEC: no analyzer ready this long after worker start -> queued jobs fail
    # fast («загрузка модели не завершилась») until one becomes ready (see app.worker).
    analyzer_load_timeout_sec: float = DEFAULT_ANALYZER_LOAD_TIMEOUT_SEC

    @property
    def api_key_configured(self) -> bool:
        return len(self.api_key.strip()) >= MIN_API_KEY_LEN

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
        api_key = (os.environ.get("API_KEY") or "").strip()
        is_dev_default = False
        if len(api_key) < MIN_API_KEY_LEN:
            if _env_bool("API_KEY_DEV_DEFAULT"):
                api_key, is_dev_default = DEV_API_KEY, True
                log.warning("!!! API_KEY_DEV_DEFAULT=1: /api/v1 accepts the PUBLIC dev key %r. "
                            "Local development only - never on a reachable deployment !!!", DEV_API_KEY)
            else:
                api_key = ""  # not configured: /api/v1/* answers 503 (logged at app startup)
        job_timeout = _env_positive_sec("JOB_TIMEOUT_SEC", DEFAULT_JOB_TIMEOUT_SEC)
        hard_cap = _env_positive_sec("JOB_HARD_CAP_SEC", DEFAULT_JOB_HARD_CAP_SEC)
        if job_timeout > hard_cap:
            log.warning("JOB_TIMEOUT_SEC=%g exceeds JOB_HARD_CAP_SEC=%g; the hard cap wins "
                        "(jobs end at most %g s after submit)", job_timeout, hard_cap, hard_cap)
        return cls(
            api_key=api_key,
            api_key_is_dev_default=is_dev_default,
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
            brand=_env_brand(),
            live_replay=_env_live_replay(),
            job_workers=_env_int_clamped("JOB_WORKERS", DEFAULT_JOB_WORKERS, 1, MAX_JOB_WORKERS),
            clip_max_parallel=_env_int_clamped("CLIP_MAX_PARALLEL", DEFAULT_CLIP_MAX_PARALLEL, 1,
                                               MAX_CLIP_MAX_PARALLEL),
            analyzer_load_timeout_sec=_env_positive_sec("ANALYZER_LOAD_TIMEOUT_SEC",
                                                        DEFAULT_ANALYZER_LOAD_TIMEOUT_SEC),
        )


def get_settings() -> Settings:
    return Settings.from_env()
