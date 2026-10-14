"""TOFU pin of peer Agent Cards. Hash mismatch = refuse the call."""

from __future__ import annotations

import hashlib
import logging
from urllib.parse import urlparse

from app.core.config import REPO_ROOT, get_settings

log = logging.getLogger("forge.a2a.pin")

PIN_DIR = REPO_ROOT / "data" / "pinned_cards"


def card_digest(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def pin_path(base_url: str):
    host = urlparse(base_url).netloc.replace(":", "_") or "peer"
    return PIN_DIR / f"{host}.sha256"


class CardPinError(RuntimeError):
    pass


def pin_or_verify(base_url: str, body: bytes) -> str:
    if not get_settings().a2a_pin_cards:
        return card_digest(body)
    if not body:
        raise CardPinError("empty Agent Card")
    digest = card_digest(body)
    path = pin_path(base_url)
    PIN_DIR.mkdir(parents=True, exist_ok=True)
    if path.exists():
        expected = path.read_text(encoding="utf-8").strip()
        if expected and expected != digest:
            raise CardPinError(
                f"Agent Card pin mismatch for {base_url}: expected {expected[:12]}… got {digest[:12]}…"
            )
        return digest
    path.write_text(digest + "\n", encoding="utf-8")
    log.info("pinned Agent Card %s %s", base_url, digest[:16])
    return digest
