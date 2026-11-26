"""Draft, screen, and deliver one explicitly approved plain-text email."""

import ipaddress
import json
import os
import re
import smtplib
import ssl
import unicodedata
from contextlib import contextmanager
from email.message import EmailMessage
from secrets import token_urlsafe
from time import time
from urllib.parse import urlsplit

from gateway import live_enabled, nim_completion, screen, screen_document
from output_guard import API_KEY
from storage import connect
import windows_secrets


REVIEW_TTL = 600
DELIVERY_STATUS_TTL = 600
GMAIL_HOST = "smtp.gmail.com"
GMAIL_PORT = 465
APP_PASSWORD = re.compile(r"[A-Za-z0-9]{16}", re.ASCII)
MAILBOX = re.compile(r"[A-Za-z0-9.!#$%&'*+/=?^_~-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+", re.ASCII)
PRIVATE_KEY = re.compile(r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----", re.I)
CREDENTIAL_REQUEST = re.compile(
    r"\b(?:send|share|provide|enter|confirm|verify|reply with|submit|disclose|upload|give)\b"
    r"[^.!?\n]{0,90}\b(?:passwords?|passcodes?|otp|one[- ]time (?:codes?|passwords?)|"
    r"recovery codes?|seed phrases?|private keys?|api keys?|"
    r"(?:credit|debit) card (?:numbers?|details))\b"
    r"|\b(?:password|otp|passcode|api key|card number)\b[^.!?\n]{0,50}"
    r"\b(?:bhejo|bhej do|batao|bata do|share karo|de do)\b"
    r"|(?:ओटीपी|पासवर्ड)[^.!?\n]{0,40}(?:भेजें|भेजो|बताएं|बताओ|शेयर करें)", re.I
)
PAYMENT_PRESSURE = re.compile(
    r"(?=.*\b(?:urgent(?:ly)?|immediately|right away|within (?:an? hour|\d+ minutes?))\b)"
    r"(?=.*\b(?:wire transfer|gift cards?|bitcoin|crypto(?:currency)?|wire (?:the )?money)\b)",
    re.I | re.S,
)
ABUSIVE_INTENT = re.compile(
    r"\b(?:blackmail|extort|doxx?|death threats?|threaten)\b"
    r"|\b(?:i|we)\s*(?:will|'ll|am going to|are going to)\s+(?:kill|hurt|attack|harm)\s+(?:you|him|her|them)\b"
    r"|\b(?:jaan se maar|maar daal|dhamki de|paise nahi diye toh)\w*\b"
    r"|(?:जान से मार|मार डाल|धमकी दे)", re.I
)
DECEPTIVE_INTENT = re.compile(
    r"\b(?:phish(?:ing)?|scam|impersonate|pose as|pretend to be)\b[^.!?\n]{0,65}"
    r"\b(?:bank|payroll|support|government|police|recipient|employee|them|customer|victim)\b"
    r"|\b(?:fake|forged|fraudulent)\s+(?:(?:bank|payment|tax)\s+)?(?:invoice|receipt|payment confirmation)\b"
    r"|\b(?:bank|support)\s+ban\s+kar\b", re.I
)
UNFINISHED_TEMPLATE = re.compile(
    r"\[(?:your (?:name|email|phone)|recipient(?:'s)? name|name|date|time|location|venue|sign(?:ature|[- ]off))\]"
    r"|\{\{[^{}\n]{1,80}\}\}", re.I
)
URL = re.compile(r"https?://[^\s<>\"']+", re.I)
UNSAFE_SCHEME = re.compile(r"\b(?:file|ftp)://|\b(?:javascript|vbscript):\s*\S|\bdata:[^\s,]{0,100},", re.I)
SHORTENERS = {"bit.ly", "t.co", "tinyurl.com", "is.gd", "ow.ly", "cutt.ly", "rb.gy", "tiny.cc"}


class EmailProblem(Exception):
    def __init__(self, message, status=400, checks=()):
        super().__init__(message)
        self.status = status
        self.checks = checks


def mailbox(value):
    if not isinstance(value, str) or len(value) > 254 or not MAILBOX.fullmatch(value):
        return False
    local, host = value.rsplit("@", 1)
    return (
        len(local) <= 64 and not local.startswith(".") and not local.endswith(".") and ".." not in local
        and all(len(label) <= 63 and not label.startswith("-") and not label.endswith("-")
                for label in host.split("."))
    )


def gmail_config():
    if os.name != "nt":
        return None
    with connect() as db:
        row = db.execute("SELECT protected_config FROM gmail_settings WHERE id = 1").fetchone()
    if row is None:
        return None
    try:
        address, password = json.loads(windows_secrets.unprotect(row["protected_config"]))
        if mailbox(address) and isinstance(password, str) and APP_PASSWORD.fullmatch(password):
            return GMAIL_HOST, GMAIL_PORT, "ssl", address, password, address
    except (OSError, ValueError, TypeError):
        pass
    return None


def account_status():
    gmail = gmail_config()
    config = gmail or environment_smtp_config()
    return {"gmail": gmail[-1] if gmail else None, "sender": config[-1] if config else None,
            "setup_available": os.name == "nt"}


def connect_gmail(data):
    if os.name != "nt":
        raise EmailProblem("In-app Gmail setup requires Windows.", 503)
    address, password = data.get("address"), data.get("password")
    if not isinstance(address, str) or not isinstance(password, str) or len(password) > 64:
        raise EmailProblem("Enter your Gmail address and a Google app password.")
    address = address.strip().lower()
    password = "".join(password.split())
    if not mailbox(address) or address.rsplit("@", 1)[1] not in {"gmail.com", "googlemail.com"}:
        raise EmailProblem("Enter a consumer Gmail address. Custom domains can use SMTP environment settings.")
    if not APP_PASSWORD.fullmatch(password):
        raise EmailProblem("Enter the 16-character Google app password.")
    config = (GMAIL_HOST, GMAIL_PORT, "ssl", address, password, address)
    try:
        with smtp_connection(config):
            pass
    except smtplib.SMTPAuthenticationError:
        raise EmailProblem("Gmail rejected the login. Check the address, 2-Step Verification, and app password.", 422) from None
    except (OSError, ValueError):
        raise EmailProblem("Could not verify Gmail over TLS. Check the network and try again.", 502) from None
    try:
        protected = windows_secrets.protect(json.dumps([address, password]).encode("utf-8"))
    except OSError:
        raise EmailProblem("Windows could not secure the app password.", 500) from None
    with connect() as db:
        db.execute("INSERT OR REPLACE INTO gmail_settings VALUES (1, ?)", (protected,))
    return account_status()


def disconnect_gmail(data):
    if data != {"confirm": True} or data.get("confirm") is not True:
        raise EmailProblem("Confirm that you want to disconnect Gmail from AgentShield.")
    with connect() as db:
        db.execute("DELETE FROM gmail_settings WHERE id = 1")
    return account_status()


def smtp_config():
    return gmail_config() or environment_smtp_config()


def environment_smtp_config():
    host = os.getenv("AGENTSHIELD_SMTP_HOST", "").strip()
    mode = os.getenv("AGENTSHIELD_SMTP_TLS", "starttls").strip().lower()
    username = os.getenv("AGENTSHIELD_SMTP_USERNAME", "").strip()
    password = os.getenv("AGENTSHIELD_SMTP_PASSWORD", "")
    sender = os.getenv("AGENTSHIELD_SMTP_FROM", username).strip()
    try:
        port = int(os.getenv("AGENTSHIELD_SMTP_PORT", "465" if mode == "ssl" else "587"))
    except ValueError:
        return None
    if not (host and username and password and mailbox(sender) and 0 < port <= 65535
            and mode in {"starttls", "ssl"}):
        return None
    return host, port, mode, username, password, sender


def gmail_secret_present(text):
    config = gmail_config()
    return bool(config and config[4] in "".join(text.split()))


def secret_present(text):
    known_secrets = (os.getenv("NVIDIA_API_KEY", "").strip(), os.getenv("AGENTSHIELD_SMTP_PASSWORD", ""))
    return (
        any(secret and secret in text for secret in known_secrets) or gmail_secret_present(text)
        or bool(API_KEY.search(text) or PRIVATE_KEY.search(text))
    )


def has_risky_link(text):
    if UNSAFE_SCHEME.search(text):
        return True
    for match in URL.finditer(text):
        try:
            target = urlsplit(match[0].rstrip(".,;!?)"))
            host = (target.hostname or "").lower().rstrip(".")
            # Accessing port verifies that an explicit port is valid.
            target.port
        except ValueError:
            return True
        if (target.scheme != "https" or target.username is not None or not host
                or "." not in host or host.endswith(".localhost") or any(
                    host == domain or host.endswith("." + domain) for domain in SHORTENERS
                )):
            return True
        try:
            ipaddress.ip_address(host)
        except ValueError:
            continue
        return True
    return False


def check_row(name, blocked, reason):
    return {"name": name, "status": "block" if blocked else "pass", "detail": reason}


def validate_content(subject, body, *, require_finished=True):
    header_ok = bool(
        isinstance(subject, str) and 0 < len(subject) <= 160 and subject.strip()
        and all(unicodedata.category(char) not in {"Cc", "Cs", "Zl", "Zp"} for char in subject)
    )
    body_ok = bool(
        isinstance(body, str) and 0 < len(body) <= 6000 and body.strip()
        and all(char == "\n" or unicodedata.category(char) not in {"Cc", "Cs"} for char in body)
    )
    checks = [
        check_row("Subject and message format", not (header_ok and body_ok),
                  "Use a single-line subject and a plain-text body within the size limits."),
    ]
    if not (header_ok and body_ok):
        return checks

    text = unicodedata.normalize("NFKC", subject + "\n" + body)
    checks.append(check_row("Prompt injection and hidden characters", bool(screen_document(text)),
                            "Screens instruction overrides, embedded roles and suspicious text patterns."))
    checks.extend(content_risk_checks(subject + "\n" + body, text))
    if require_finished:
        checks.append(check_row("Unfilled template fields", bool(UNFINISHED_TEMPLATE.search(text)),
                                "Fill in or remove any placeholders before sending."))
    return checks


def content_risk_checks(original, normalized):
    return [
        check_row("Credentials and private keys",
                  secret_present(original) or (original != normalized and secret_present(normalized)),
                  "Checks configured secrets and recognizable credential patterns."),
        check_row("Credential requests", bool(CREDENTIAL_REQUEST.search(normalized)),
                  "Checks for requests to disclose passwords, OTPs, or similar credentials."),
        check_row("Urgent payment requests", bool(PAYMENT_PRESSURE.search(normalized)),
                  "Checks for payment-pressure phrases involving wire transfers, gift cards, or crypto."),
        check_row("Threats and deceptive requests",
                  bool(ABUSIVE_INTENT.search(normalized) or DECEPTIVE_INTENT.search(normalized)),
                  "Checks for threats, extortion, impersonation, and fabricated payment documents."),
        check_row("Suspicious links", has_risky_link(normalized),
                  "Checks explicit links for insecure schemes, shorteners, IPs, and misleading user-info."),
    ]


def enforce(checks):
    if any(check["status"] == "block" for check in checks):
        raise EmailProblem("Email blocked by security checks.", 422, checks)


def generate_draft(data):
    prompt = data.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 2000:
        raise EmailProblem("Describe the email in 1 to 2,000 characters.")
    prompt = prompt.strip()
    try:
        prompt.encode("utf-8")
    except UnicodeError:
        raise EmailProblem("The email instruction contains invalid text.") from None
    normalized = unicodedata.normalize("NFKC", prompt)
    checks = [
        check_row("Draft instruction screening", any(check["status"] == "block" for check in screen(prompt)),
                  "Screens the instruction before contacting NVIDIA."),
        *content_risk_checks(prompt, normalized),
    ]
    enforce(checks)
    if not live_enabled():
        raise EmailProblem("Configure NVIDIA to generate drafts, or write the email below.", 503)
    messages = [
        {"role": "system", "content": (
            "Write a plain-text email draft from the user's instructions. You can only draft. "
            "Return one JSON object with exactly two string fields, subject and body. "
            "Keep the subject to one line and the body under 6,000 characters. "
            "Do not include markdown fences, email headers or recipient addresses. "
            "Use the user's language. Never invent facts, dates, links or contact details. "
            "Omit a sender signature if no name was supplied. Avoid bracketed placeholders. "
            "If the request would produce a phishing, scam, extortion, targeted threat, impersonation, "
            "or credential-theft email, return exactly the JSON object {\"blocked\":true} instead."
        )},
        {"role": "user", "content": prompt},
    ]
    try:
        response = nim_completion(messages)
        if response.get("tool_calls"):
            raise ValueError
        raw = response["content"].strip()
        fenced = re.fullmatch(r"\x60\x60\x60(?:json)?\s*(.*?)\s*\x60\x60\x60", raw, flags=re.S | re.I)
        draft = json.loads(fenced[1] if fenced else raw)
        if not isinstance(draft, dict):
            raise ValueError
        if set(draft) == {"blocked"} and draft["blocked"] is True:
            enforce([*checks, check_row("AI draft safety", True,
                                       "The model declined to write a potentially harmful email.")])
        if set(draft) != {"subject", "body"}:
            raise ValueError
    except (AttributeError, OSError, ValueError, IndexError, KeyError, TypeError):
        raise EmailProblem("NVIDIA could not return a usable email draft.", 502) from None
    checks.extend(validate_content(draft["subject"], draft["body"], require_finished=False))
    enforce(checks)
    return {**draft, "checks": checks}


def review(data):
    recipient, subject, body = (data.get(key) for key in ("recipient", "subject", "body"))
    checks = [
        check_row("Single recipient", not mailbox(recipient) or secret_present(recipient),
                  "Enter one email address without display names, separators, or credentials."),
        *validate_content(subject, body),
    ]
    enforce(checks)
    config = smtp_config()
    sender = config[-1] if config else None
    snapshot = {"sender": sender, "recipient": recipient, "subject": subject, "body": body}
    result = {**snapshot, "checks": checks, "token": None}
    if not sender:
        return result
    token = token_urlsafe(32)
    now = time()
    with connect() as db:
        db.execute("DELETE FROM email_reviews WHERE expires_at < ? AND status = 'ready'", (now,))
        db.execute("INSERT INTO email_reviews VALUES (?, ?, ?, 'ready')",
                   (token, now + REVIEW_TTL, json.dumps(snapshot, ensure_ascii=False)))
    return {**result, "token": token, "expires_in": REVIEW_TTL}


def valid_token(token):
    return isinstance(token, str) and bool(re.fullmatch(r"[A-Za-z0-9_-]{43}", token))


def review_status(row):
    if row["status"] == "sending" and row["expires_at"] <= time():
        return "uncertain"
    return row["status"]


def delivery_status(data):
    token = data.get("token")
    if set(data) != {"token"} or not valid_token(token):
        raise EmailProblem("Open a valid email review.")
    with connect() as db:
        row = db.execute("SELECT status, expires_at FROM email_reviews WHERE token = ?", (token,)).fetchone()
    if row is None or (row["status"] == "ready" and row["expires_at"] <= time()):
        raise EmailProblem("This review is unavailable. Review the email again.", 409)
    return {"status": review_status(row)}


def send(data):
    token = data.get("token")
    if set(data) != {"token", "confirm"} or data.get("confirm") is not True or not valid_token(token):
        raise EmailProblem("Review the email and explicitly confirm Send.")
    config = smtp_config()
    if config is None:
        raise EmailProblem("Email delivery is not configured.", 503)
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT * FROM email_reviews WHERE token = ?", (token,)).fetchone()
        if row is None:
            raise EmailProblem("This review is unavailable. Review the email again.", 409)
        if row["status"] != "ready":
            return {"status": review_status(row), "checks": []}
        if row["expires_at"] <= time():
            raise EmailProblem("This review expired. Review the email again.", 409)
        draft = json.loads(row["snapshot"])
        checks = [
            check_row("Reviewed sender and recipient",
                      draft["sender"] != config[-1] or not mailbox(draft["recipient"])
                      or secret_present(draft["recipient"]),
                      "The sending account and recipient must pass verification."),
            *validate_content(draft["subject"], draft["body"]),
        ]
        enforce(checks)
        db.execute("UPDATE email_reviews SET status = 'sending', snapshot = '{}', expires_at = ? WHERE token = ?",
                   (time() + DELIVERY_STATUS_TTL, token))

    # A dropped connection during SMTP submission may leave delivery uncertain.
    # The same approval is never reused, including after a browser retry.
    status = deliver(config, draft)
    with connect() as db:
        db.execute("UPDATE email_reviews SET status = ? WHERE token = ?", (status, token))
    return {"status": status, "checks": checks}


@contextmanager
def smtp_connection(config):
    host, port, mode, username, password, _ = config
    context = ssl.create_default_context()
    client = None
    try:
        if mode == "ssl":
            client = smtplib.SMTP_SSL(host, port, timeout=20, context=context)
        else:
            client = smtplib.SMTP(host, port, timeout=20)
            client.starttls(context=context)
        client.login(username, password)
        yield client
    finally:
        if client is not None:
            try:
                client.close()
            except OSError:
                pass


def deliver(config, draft):
    sender = config[-1]
    message = EmailMessage()
    message["From"] = sender
    message["To"] = draft["recipient"]
    message["Subject"] = draft["subject"]
    message.set_content(draft["body"])

    submitting = False
    try:
        with smtp_connection(config) as client:
            submitting = True
            refused = client.send_message(message, from_addr=sender, to_addrs=[draft["recipient"]])
            return "failed" if refused else "sent"
    except (smtplib.SMTPRecipientsRefused, smtplib.SMTPSenderRefused, smtplib.SMTPDataError):
        return "failed"
    except (OSError, ValueError):
        return "uncertain" if submitting else "failed"
