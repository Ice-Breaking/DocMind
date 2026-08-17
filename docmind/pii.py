"""PII detection and masking for Chinese context."""
import re

# PII patterns
_EMAIL_RE = re.compile(r'[\w.-]+@[\w-]+\.[\w.-]+')
_PHONE_RE = re.compile(r'(?<!\d)1[3-9]\d{9}(?!\d)')
_ID_CARD_RE = re.compile(r'(?<!\d)\d{6}(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx](?!\d)')
_BANK_CARD_RE = re.compile(r'(?<!\d)\d{16,19}(?!\d)')


def mask_pii(text: str) -> str:
    """Replace PII patterns with placeholders."""
    if not text:
        return text
    text = _EMAIL_RE.sub('[EMAIL]', text)
    text = _PHONE_RE.sub('[PHONE]', text)
    text = _ID_CARD_RE.sub('[ID_CARD]', text)
    # Skip bank card to reduce false positives on long numbers
    return text


def contains_pii(text: str) -> bool:
    """Check if text contains any PII patterns."""
    if not text:
        return False
    return bool(
        _EMAIL_RE.search(text) or
        _PHONE_RE.search(text) or
        _ID_CARD_RE.search(text)
    )
