"""Recognize birthday/anniversary wishes the owner sends, for manual review later.

Detection is deliberately phrase-based (no model call): a wish only creates a
candidate, and the owner decides whether it becomes a greeting occasion.
"""

import re
from dataclasses import dataclass
from typing import Any

BIRTHDAY = re.compile(
    r"\b(?:happy|hapy|hppy|hpy)\s*(?:b'?day|bday|birthday|birth\s+day|budday|bdy)\b"
    r"|\bhbd\b|\bmany\s+happy\s+returns\b|\bjanam\s*din\b|\bjanmdin\b|\bjanamdin\b"
    r"|जन्मदिन",
    re.IGNORECASE,
)
ANNIVERSARY = re.compile(
    r"\bhappy\s+(?:wedding\s+|marriage\s+)?anniversary\b|\bhappy\s+anni\b"
    r"|\bsalgirah\b|सालगिरह",
    re.IGNORECASE,
)
BELATED = re.compile(r"\bbelated\b|\bsorry\s+(?:i'?m\s+)?late\b", re.IGNORECASE)
# Words that follow a wish but are not a name ("Happy birthday dear bro!").
NOT_NAMES = {
    "a", "and", "anni", "anniversary", "bhai", "bhaiya", "bday", "birthday", "bro",
    "brother", "buddy", "day", "dear", "didi", "dude", "friend", "from", "god", "great",
    "have", "hope", "ji", "love", "man", "many", "mate", "mubarak", "my", "of", "once",
    "returns", "sir", "sis", "sister", "the", "to", "wish", "wishing", "you", "your",
    # Hindi/Hinglish wish phrases ("janamdin ki shubhkamnayein", "bahut badhai ho").
    "badhai", "bahut", "ho", "ka", "ke", "ki", "shubh", "shubhkamna", "shubhkamnaye",
    "shubhkamnayein", "subhkamnaye",
}  # fmt: skip
NAME_WORD = re.compile(r"[A-Za-z][A-Za-z.'-]*")

# Calling codes of common countries that use a single timezone. Others (e.g. +1,
# +7, +61) span several zones, so the owner is asked instead of guessing.
CALLING_CODES = {
    "91": ("IN", "Asia/Kolkata"),
    "44": ("GB", "Europe/London"),
    "353": ("IE", "Europe/Dublin"),
    "971": ("AE", "Asia/Dubai"),
    "966": ("SA", "Asia/Riyadh"),
    "974": ("QA", "Asia/Qatar"),
    "965": ("KW", "Asia/Kuwait"),
    "968": ("OM", "Asia/Muscat"),
    "973": ("BH", "Asia/Bahrain"),
    "92": ("PK", "Asia/Karachi"),
    "880": ("BD", "Asia/Dhaka"),
    "977": ("NP", "Asia/Kathmandu"),
    "94": ("LK", "Asia/Colombo"),
    "65": ("SG", "Asia/Singapore"),
    "60": ("MY", "Asia/Kuala_Lumpur"),
    "81": ("JP", "Asia/Tokyo"),
    "86": ("CN", "Asia/Shanghai"),
    "27": ("ZA", "Africa/Johannesburg"),
    "33": ("FR", "Europe/Paris"),
    "49": ("DE", "Europe/Berlin"),
    "39": ("IT", "Europe/Rome"),
    "31": ("NL", "Europe/Amsterdam"),
    "64": ("NZ", "Pacific/Auckland"),
}


@dataclass(frozen=True)
class Wish:
    occasion: str
    name: str | None
    belated: bool


def detect_wish(text: str) -> Wish | None:
    """The occasion and any name written straight after the wish, e.g. 'HBD Rahul'."""
    match = ANNIVERSARY.search(text) or BIRTHDAY.search(text)
    if not match:
        return None
    occasion = "anniversary" if match.re is ANNIVERSARY else "birthday"
    words = []
    for token in text[match.end() :].split()[:6]:
        word = NAME_WORD.match(token.strip(",!.:;-~()"))
        if token.startswith("@") or not word or word.group(0).lower() in NOT_NAMES:
            if words:
                break
            continue  # Skip leading "dear", "to", "my" before the name.
        words.append(word.group(0).strip(".'-"))
        if len(words) == 3 or not token[-1].isalnum():
            break  # Punctuation or emoji right after a word ends the name.
    return Wish(
        occasion, " ".join(w.capitalize() for w in words) or None, bool(BELATED.search(text))
    )


def recipient(item: dict[str, Any], is_group: bool) -> str | None:
    """Who the wish is for: the personal chat, or the person mentioned/replied to."""
    key = item.get("key") if isinstance(item.get("key"), dict) else {}
    if not is_group:
        alt = key.get("remoteJidAlt")
        return alt if isinstance(alt, str) and alt else key.get("remoteJid")
    message = item.get("message") if isinstance(item.get("message"), dict) else {}
    for value in message.values():
        info = value.get("contextInfo") if isinstance(value, dict) else None
        if not isinstance(info, dict):
            continue
        mentioned = info.get("mentionedJid")
        if isinstance(mentioned, list) and mentioned and isinstance(mentioned[0], str):
            return mentioned[0]
        if isinstance(info.get("participant"), str):
            return info["participant"]  # The author of the message being replied to.
    return None


def phone_from_jid(jid: str | None) -> str | None:
    """Phone JIDs carry the number; private LIDs (…@lid) do not."""
    if not jid or not jid.endswith("@s.whatsapp.net"):
        return None
    digits = jid.split("@", 1)[0].split(":", 1)[0]
    return "+" + digits if digits.isdigit() else None


def region(phone: str | None) -> tuple[str, str] | None:
    """(country, timezone) for a phone number in a single-timezone country."""
    digits = (phone or "").lstrip("+")
    for length in (3, 2):
        if digits[:length] in CALLING_CODES:
            return CALLING_CODES[digits[:length]]
    return None
