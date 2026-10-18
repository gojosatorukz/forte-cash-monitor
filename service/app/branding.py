"""UI branding (BRAND=forte|neutral), applied server-side so a page never flashes the wrong logo.

The UI pages (index.html, act.html) mark brand-specific markup with
``<!--brand:forte-->…<!--/brand:forte-->`` / ``<!--brand:neutral-->…<!--/brand:neutral-->``.
`render_page` keeps the active brand's blocks, drops the other's, sets ``<html data-brand="…">``
(CSS hides `.brand-forte` / `.brand-neutral` by it as a fallback) and points the favicon at the
brand's icon. forte output is the static file minus the neutral blocks (same look as before).
The same pass sets ``<html data-live="1|0">`` (LIVE_REPLAY): app.js skips the live replay on 0.
"""

from __future__ import annotations

import re
import threading
from pathlib import Path

from app.config import BRANDS, DEFAULT_BRAND

FAVICONS = {"forte": "assets/brand/favicon.svg", "neutral": "assets/brand/favicon-neutral.svg"}
# URL path -> static file served through `render_page` (everything else stays on the static mount).
BRANDED_PAGES = {"/": "index.html", "/index.html": "index.html", "/act.html": "act.html"}

_BLOCK = re.compile(r"<!--brand:(forte|neutral)-->(.*?)<!--/brand:\1-->", re.S)
_HTML_TAG = re.compile(r"<html\b([^>]*)>", re.I)


def normalize_brand(value: str | None) -> str:
    v = (value or "").strip().lower()
    return v if v in BRANDS else DEFAULT_BRAND


def apply_brand(page: str, brand: str, live: bool | None = None) -> str:
    """`live` None leaves data-live out (page default: on); True/False -> data-live="1"/"0"."""
    brand = normalize_brand(brand)
    attrs = f' data-brand="{brand}"' + ("" if live is None else f' data-live="{1 if live else 0}"')
    out = _BLOCK.sub(lambda m: m.group(2) if m.group(1) == brand else "", page)
    out = _HTML_TAG.sub(lambda m: f'<html{m.group(1)}{attrs}>', out, count=1)
    out = out.replace(f'href="{FAVICONS[DEFAULT_BRAND]}"', f'href="{FAVICONS[brand]}"')
    if brand == "neutral":
        out = out.replace("Forte Cash Monitor", "Cash Monitor")
    return out


class BrandedPages:
    """Reads each page once (first request), brands it and caches the result for the process lifetime."""

    def __init__(self, static_dir: Path, brand: str, live: bool = True):
        self.static_dir = Path(static_dir)
        self.brand = normalize_brand(brand)
        self.live = bool(live)
        self._cache: dict[str, bytes] = {}
        self._lock = threading.Lock()

    def get(self, name: str) -> bytes:
        body = self._cache.get(name)
        if body is None:
            with self._lock:
                body = self._cache.get(name)
                if body is None:
                    page = (self.static_dir / name).read_text(encoding="utf-8")
                    body = apply_brand(page, self.brand, self.live).encode("utf-8")
                    self._cache[name] = body
        return body
