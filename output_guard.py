"""Filter final model replies before display or persistence."""

import re


EMAIL = re.compile(
    r"(?<![\w.!#$%&'*+/=?^`{|}~@-])[A-Z0-9.!#$%&'*+/=?^`{|}~+-]+"
    r"@[A-Z0-9](?:[A-Z0-9-]*[A-Z0-9])?(?:\.[A-Z0-9](?:[A-Z0-9-]*[A-Z0-9])?)+"
    r"(?![\w@-])",
    re.IGNORECASE | re.ASCII,
)
API_KEY = re.compile(r"(?<![\w-])(?:nvapi-|sk-(?:proj-)?)[A-Za-z0-9_-]{20,}(?![\w-])")
CITATION = re.compile(r"\[\s*(S[0-9]+(?:\s*[,;]\s*S[0-9]+)*)\s*\]", re.IGNORECASE | re.ASCII)


def review_reply(text, verified_refs=None, configured_key=""):
    """Return sanitized text and check counts without retaining matched values."""
    credentials = 0
    if configured_key:
        credentials = text.count(configured_key)
        text = text.replace(configured_key, "[REDACTED API KEY]")
    text, keys = API_KEY.subn("[REDACTED API KEY]", text)
    text, emails = EMAIL.subn("[REDACTED EMAIL]", text)
    checks = [
        {"name": "Email addresses", "count": emails},
        {"name": "API key patterns", "count": credentials + keys},
    ]
    if verified_refs is not None:
        withheld = 0

        def verify_citation(match):
            nonlocal withheld
            markers = []
            for ref in re.split(r"\s*[,;]\s*", match[1].upper()):
                if ref in verified_refs:
                    markers.append("[" + ref + "]")
                else:
                    withheld += 1
                    markers.append("[UNVERIFIED SOURCE]")
            return " ".join(markers)

        text = CITATION.sub(verify_citation, text)
        checks.append({"name": "Unverified source references", "count": withheld})
    return text, checks
