"""外发 DLP：Presidio + 密钥/证件正则。写盘、提交、bash、工具回传都过这里。"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from functools import lru_cache

from app.services.safety import SafetyError

log = logging.getLogger("forge.dlp")

SECRET_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("openai_key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b")),
    ("anthropic_key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}\b")),
    ("github_pat", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b")),
    ("github_finegrained", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b")),
    ("aws_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("stripe_key", re.compile(r"\b[srk]k_live_[A-Za-z0-9]{20,}\b")),
    ("docker_token", re.compile(r"\bdckr_pat_[A-Za-z0-9_-]{20,}\b")),
    ("slack_token", re.compile(r"\bxox[baprs]-[0-9A-Za-z-]{10,}\b")),
    ("private_key", re.compile(r"-----BEGIN (?:RSA |OPENSSH |EC |DSA )?PRIVATE KEY-----")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")),
    ("password_assign", re.compile(r"(?i)\b(password|passwd|api_key|secret|token)\s*[:=]\s*['\"][^'\"]{8,}['\"]")),
]
PII_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("cn_id", re.compile(r"\b[1-9]\d{5}(?:18|19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx]\b")),
    ("cn_mobile", re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")),
    ("credit_card", re.compile(r"\b(?:\d[ -]*?){13,19}\b")),
    ("email", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")),
]
BLOCK_TYPES = {
    "openai_key",
    "anthropic_key",
    "github_pat",
    "github_finegrained",
    "aws_key",
    "google_api_key",
    "stripe_key",
    "docker_token",
    "slack_token",
    "private_key",
    "jwt",
    "password_assign",
    "credit_card",
    "CREDIT_CARD",
    "CRYPTO",
    "IBAN_CODE",
    "US_BANK_NUMBER",
    "US_ITIN",
    "US_SSN",
    "cn_id",
}
EGRESS_RE = re.compile(r"\b(curl|wget|scp|sftp|rsync|nc|ncat|netcat|ssh)\b", re.I)

_PRESIDIO_READY: bool | None = None


@dataclass
class ScanResult:
    hits: list[str] = field(default_factory=list)
    blocked: list[str] = field(default_factory=list)

    @property
    def should_block(self) -> bool:
        return bool(self.blocked)


def _luhn_ok(digits: str) -> bool:
    if not 13 <= len(digits) <= 19:
        return False
    total = 0
    alt = False
    for ch in digits[::-1]:
        n = ord(ch) - 48
        if alt:
            n *= 2
            if n > 9:
                n -= 9
        total += n
        alt = not alt
    return total % 10 == 0


def regex_scan(text: str) -> ScanResult:
    hits: list[str] = []
    if not text:
        return ScanResult()
    for name, pat in SECRET_PATTERNS:
        if pat.search(text):
            hits.append(name)
    for name, pat in PII_PATTERNS:
        if name == "credit_card":
            for m in pat.finditer(text):
                digits = re.sub(r"\D", "", m.group(0))
                if _luhn_ok(digits):
                    hits.append("credit_card")
                    break
            continue
        if name == "email":
            # 源码里邮箱太多，写盘不拦；只在回传脱敏时用
            if pat.search(text):
                hits.append("email")
            continue
        if pat.search(text):
            hits.append(name)
    blocked = [h for h in hits if h in BLOCK_TYPES]
    return ScanResult(hits=hits, blocked=blocked)


@lru_cache(maxsize=1)
def _presidio_engine():
    try:
        from presidio_analyzer import AnalyzerEngine, Pattern, PatternRecognizer, RecognizerRegistry

        registry = RecognizerRegistry()
        try:
            registry.load_predefined_recognizers(languages=["en"])
        except Exception:
            log.warning("Presidio predefined recognizers unavailable; using pattern recognizers only")
        registry.add_recognizer(
            PatternRecognizer(
                supported_entity="CN_ID",
                name="cn_id",
                patterns=[
                    Pattern(
                        name="cn_id",
                        regex=r"[1-9]\d{5}(18|19|20)\d{2}(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])\d{3}[\dXx]",
                        score=0.7,
                    )
                ],
            )
        )
        return AnalyzerEngine(registry=registry, supported_languages=["en"])
    except Exception:
        log.warning("Presidio AnalyzerEngine failed to start; regex DLP still active", exc_info=True)
        return None


def presidio_scan(text: str) -> list[str]:
    global _PRESIDIO_READY
    if not text:
        return []
    engine = _presidio_engine()
    if engine is None:
        _PRESIDIO_READY = False
        return []
    try:
        results = engine.analyze(text=text[:20_000], language="en")
        _PRESIDIO_READY = True
        return sorted({r.entity_type for r in results if r.score >= 0.6})
    except Exception:
        log.warning("Presidio analyze failed", exc_info=True)
        _PRESIDIO_READY = False
        return []


def scan_text(text: str) -> ScanResult:
    regex = regex_scan(text)
    extra = presidio_scan(text)
    hits = list(dict.fromkeys([*regex.hits, *extra]))
    blocked = [h for h in hits if h in BLOCK_TYPES]
    return ScanResult(hits=hits, blocked=blocked)


def redact_text(text: str) -> str:
    if not text:
        return text
    out = text
    for _name, pat in SECRET_PATTERNS:
        out = pat.sub("[SECRET]", out)
    out = PII_PATTERNS[0][1].sub("[REDACTED_ID]", out)
    out = PII_PATTERNS[1][1].sub("[REDACTED_PHONE]", out)

    def _card(m: re.Match[str]) -> str:
        digits = re.sub(r"\D", "", m.group(0))
        return "[REDACTED_CARD]" if _luhn_ok(digits) else m.group(0)

    out = PII_PATTERNS[2][1].sub(_card, out)
    try:
        engine = _presidio_engine()
        if engine is not None:
            from presidio_anonymizer import AnonymizerEngine

            results = engine.analyze(text=out[:20_000], language="en")
            if results:
                out = AnonymizerEngine().anonymize(text=out[:20_000], analyzer_results=results).text + out[20_000:]
    except Exception:
        log.debug("Presidio anonymizer skipped", exc_info=True)
    return out


def assert_safe_to_persist(text: str, *, where: str) -> None:
    result = scan_text(text)
    if result.should_block:
        kinds = "、".join(result.blocked)
        raise SafetyError(f"拒绝：{where} 含敏感数据（{kinds}）。这是 DLP 闸门，不是模型建议。")


def assert_no_egress(command: str) -> None:
    if EGRESS_RE.search(command or ""):
        raise SafetyError("拒绝：禁止在工具里发起 curl/wget/scp/ssh 等外发。沙箱也默认无网。")
