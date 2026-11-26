"""Local SQLite storage for conversations and reviewed knowledge sources."""

import hashlib
import json
import os
import re
import sqlite3
import unicodedata
from contextlib import contextmanager
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from uuid import uuid4

from pypdf import PdfReader
from pypdf.errors import PyPdfError


def now():
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def connect():
    base = Path(os.getenv("LOCALAPPDATA", Path.home())) / "AgentShield"
    path = Path(os.getenv("AGENTSHIELD_DB", base / "workspace.sqlite3"))
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def initialize():
    with connect() as db:
        db.execute("PRAGMA journal_mode = WAL")
        db.executescript("""
            CREATE TABLE IF NOT EXISTS conversations (
                id TEXT PRIMARY KEY, title TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS turns (
                id INTEGER PRIMARY KEY, conversation_id TEXT NOT NULL REFERENCES conversations(id),
                prompt TEXT NOT NULL, result TEXT NOT NULL, status TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS turns_by_conversation ON turns(conversation_id, id);
            CREATE TABLE IF NOT EXISTS documents (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, state TEXT NOT NULL,
                approved_digest TEXT, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS passages (
                document_id TEXT NOT NULL REFERENCES documents(id), ordinal INTEGER NOT NULL,
                location TEXT NOT NULL, text TEXT NOT NULL,
                PRIMARY KEY(document_id, ordinal)
            );
            CREATE TABLE IF NOT EXISTS gmail_settings (
                id INTEGER PRIMARY KEY CHECK (id = 1), protected_config BLOB NOT NULL
            );
            CREATE TABLE IF NOT EXISTS email_reviews (
                token TEXT PRIMARY KEY, expires_at REAL NOT NULL,
                snapshot TEXT NOT NULL, status TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS source_audits (
                document_id TEXT PRIMARY KEY REFERENCES documents(id),
                fingerprint TEXT NOT NULL, findings TEXT NOT NULL, scanned_at TEXT NOT NULL
            );
        """)


def conversations():
    with connect() as db:
        return [dict(row) for row in db.execute("SELECT * FROM conversations ORDER BY updated_at DESC")]


def load_conversation(identifier):
    with connect() as db:
        thread = db.execute("SELECT * FROM conversations WHERE id = ?", (identifier,)).fetchone()
        if thread is None:
            return None
        rows = db.execute("SELECT prompt, result FROM turns WHERE conversation_id = ? ORDER BY id", (identifier,))
        return {**dict(thread), "turns": [{"prompt": row["prompt"], "result": json.loads(row["result"])} for row in rows]}


def create_conversation(prompt):
    identifier = uuid4().hex
    title = " ".join(prompt.split())[:64]
    with connect() as db:
        db.execute("INSERT INTO conversations VALUES (?, ?, ?)", (identifier, title, now()))
    return identifier


def history(identifier):
    with connect() as db:
        records = db.execute("""
            SELECT prompt, result FROM turns
            WHERE conversation_id = ? AND status = 'Answered'
            ORDER BY id DESC LIMIT 6
        """, (identifier,)).fetchall()
    messages = []
    for row in reversed(records):
        answer = json.loads(row["result"])
        # A knowledge result is tied to the approval state at the time of that turn.
        if answer["sources"]:
            continue
        messages.extend((
            {"role": "user", "content": row["prompt"]},
            {"role": "assistant", "content": answer["reply"]},
        ))
    return messages


def save_turn(identifier, prompt, result):
    with connect() as db:
        db.execute("INSERT INTO turns(conversation_id, prompt, result, status) VALUES (?, ?, ?, ?)",
                   (identifier, prompt, json.dumps(result, ensure_ascii=False), result["status"]))
        db.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (now(), identifier))


def digest(name, passages):
    canonical = json.dumps([name, [(item["location"], item["text"]) for item in passages]], ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def record_audit(db, identifier, name, passages, scanner):
    findings = [{**finding, "location": "File name", "passage": None} for finding in scanner(name)]
    for index, part in enumerate(passages, 1):
        findings.extend({**finding, "location": part["location"], "passage": index}
                        for finding in scanner(part["text"]))
    fingerprint = digest(name, passages)
    db.execute("INSERT OR REPLACE INTO source_audits VALUES (?, ?, ?, ?)",
               (identifier, fingerprint, json.dumps(findings, ensure_ascii=False), now()))
    return fingerprint, findings


def backfill_source_audits(scanner):
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        rows = db.execute("""
            SELECT d.id, d.name, d.state FROM documents d
            LEFT JOIN source_audits a ON a.document_id = d.id WHERE a.document_id IS NULL
        """).fetchall()
        for row in rows:
            parts = db.execute("SELECT location, text FROM passages WHERE document_id = ? ORDER BY ordinal",
                               (row["id"],)).fetchall()
            _, findings = record_audit(db, row["id"], row["name"], parts, scanner)
            if findings and row["state"] != "archived":
                db.execute("UPDATE documents SET state = 'quarantined', approved_digest = NULL WHERE id = ?", (row["id"],))


def documents():
    with connect() as db:
        rows = db.execute("""
            SELECT d.id, d.name, d.state, d.created_at, COUNT(p.ordinal) AS passages
            FROM documents d JOIN passages p ON p.document_id = d.id
            GROUP BY d.id ORDER BY d.created_at DESC
        """)
        return [dict(row) for row in rows]


def split_passages(location, text):
    text = unicodedata.normalize("NFKC", text).replace("\x00", "").strip()
    offset = 0
    while offset < len(text):
        end = min(offset + 1250, len(text))
        if end < len(text):
            space = text.rfind(" ", offset + 850, end)
            if space != -1:
                end = space
        yield {"location": location, "text": text[offset:end].strip()}
        if end == len(text):
            break
        offset = max(offset + 1, end - 130)


def import_document(name, body, scanner):
    name = name.replace("\\", "/").split("/")[-1].strip()
    if not name or len(name) > 180:
        raise ValueError("Use a file name between 1 and 180 characters.")
    suffix = Path(name).suffix.lower()
    if not 0 < len(body) <= 2_000_000:
        raise ValueError("Use a file of up to 2 MB.")
    try:
        if suffix == ".pdf":
            pdf = PdfReader(BytesIO(body))
            if pdf.is_encrypted or len(pdf.pages) > 40:
                raise ValueError("Use an unlocked PDF of up to 40 pages.")
            pages = ((f"Page {index}", page.extract_text() or "") for index, page in enumerate(pdf.pages, 1))
        elif suffix in (".txt", ".md"):
            pages = (("Text", body.decode("utf-8-sig")),)
        else:
            raise ValueError("Choose a PDF, TXT or Markdown file.")
        passages = []
        length = 0
        for location, page_text in pages:
            length += len(page_text)
            if length > 120_000:
                raise ValueError("The extracted text exceeds 120,000 characters.")
            passages.extend(split_passages(location, page_text))
    except UnicodeDecodeError as failure:
        raise ValueError("Save this text file as UTF-8 and upload it again.") from failure
    except (PyPdfError, OSError, RecursionError, TypeError, KeyError) as failure:
        raise ValueError("The PDF could not be read.") from failure
    if not passages:
        raise ValueError("No selectable text was found in the file.")
    identifier = uuid4().hex
    with connect() as db:
        db.execute("INSERT INTO documents VALUES (?, ?, 'pending', NULL, ?)", (identifier, name, now()))
        db.executemany("INSERT INTO passages VALUES (?, ?, ?, ?)", (
            (identifier, index, part["location"], part["text"]) for index, part in enumerate(passages, 1)
        ))
        _, findings = record_audit(db, identifier, name, passages, scanner)
        if findings:
            db.execute("UPDATE documents SET state = 'quarantined' WHERE id = ?", (identifier,))
    return identifier


def source(identifier):
    with connect() as db:
        row = db.execute("""
            SELECT d.name, d.state, d.approved_digest, a.findings, a.scanned_at, a.fingerprint FROM documents d
            LEFT JOIN source_audits a ON a.document_id = d.id WHERE d.id = ?
        """, (identifier,)).fetchone()
        if row is None:
            return None
        passages = [dict(part) for part in db.execute(
            "SELECT location, text FROM passages WHERE document_id = ? ORDER BY ordinal", (identifier,))]
    return {"id": identifier, "name": row["name"], "state": row["state"], "passages": passages,
            "integrity_ok": row["state"] == "approved" and row["approved_digest"] == digest(row["name"], passages),
            "findings": json.loads(row["findings"] or "[]"), "scanned_at": row["scanned_at"],
            "fingerprint": row["fingerprint"]}


def set_document_state(identifier, state, scanner):
    if state not in ("approved", "archived", "pending"):
        raise ValueError("Unknown source action.")
    refuse_approval = False
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT name FROM documents WHERE id = ?", (identifier,)).fetchone()
        if row is None:
            return False
        approved_digest = None
        if state != "archived":
            parts = db.execute("SELECT location, text FROM passages WHERE document_id = ? ORDER BY ordinal",
                               (identifier,)).fetchall()
            fingerprint, findings = record_audit(db, identifier, row["name"], parts, scanner)
            if findings:
                refuse_approval = state == "approved"
                state = "quarantined"
            elif state == "approved":
                approved_digest = fingerprint
        db.execute("UPDATE documents SET state = ?, approved_digest = ? WHERE id = ?",
                   (state, approved_digest, identifier))
    if refuse_approval:
        raise ValueError("AgentShield found instruction-like content. Upload a clean revision before approval.")
    return True


def terms(text):
    ignored = {"the", "of", "are", "is", "my", "for", "to", "in", "and", "what", "does", "say",
               "this", "that", "from", "with", "about", "can", "how"}
    return set(re.findall(r"\w{2,}", text.casefold())) - ignored


def retrieve(query, scanner, source_id=None):
    keywords = terms(query)
    if not keywords and not source_id:
        return [], [], 0
    ranked = []
    with connect() as db:
        rows = db.execute("""
            SELECT d.id, d.name, d.state, d.approved_digest, a.findings
            FROM documents d LEFT JOIN source_audits a ON a.document_id = d.id
            WHERE d.state <> 'archived' AND (? IS NULL OR d.id = ?)
        """, (source_id, source_id))
        for row in rows:
            parts = db.execute("SELECT ordinal, location, text FROM passages WHERE document_id = ? ORDER BY ordinal",
                               (row["id"],)).fetchall()
            title_match = len(terms(row["name"]) & keywords)
            hits = []
            for part in parts:
                match = len(terms(part["text"]) & keywords)
                if match:
                    hits.append((match * 2 + title_match, dict(part)))
            if not hits and source_id:
                # A broad request such as "summarize this attachment" can use its opening passages.
                hits = [(0, dict(part)) for part in parts[:4]]
            if hits:
                intact = row["state"] == "approved" and row["approved_digest"] == digest(row["name"], parts)
                ranked.extend((score, dict(row), part, intact) for score, part in hits)
    ranked.sort(key=lambda item: item[0], reverse=True)
    verified, excluded = [], {}
    candidates = [item for item in ranked if item[3]][:18] + [item for item in ranked if not item[3]][:8]
    screened_documents = set()
    for _, document, part, intact in candidates:
        reason = None
        findings = []
        if document["state"] == "quarantined":
            reason = "Possible document injection was detected during source screening."
            findings = json.loads(document["findings"] or "[]")[:5]
        elif document["state"] != "approved":
            reason = "This source is awaiting approval."
        elif not intact:
            reason = "The source no longer matches its approved fingerprint."
        else:
            findings = [{**finding, "location": part["location"], "passage": part["ordinal"]}
                        for finding in scanner(part["text"])]
            if document["id"] not in screened_documents:
                findings += [{**finding, "location": "File name", "passage": None}
                             for finding in scanner(document["name"])]
                screened_documents.add(document["id"])
            if findings:
                reason = "Possible document injection was detected during retrieval screening."
        if reason:
            blocked = excluded.setdefault(document["id"], {"name": document["name"], "reason": reason, "findings": []})
            blocked["findings"].extend(finding for finding in findings if finding not in blocked["findings"])
        else:
            verified.append({"id": document["id"], "name": document["name"],
                             "location": part["location"], "text": part["text"]})
    verified = [part for part in verified if part["id"] not in excluded][:4]
    for index, part in enumerate(verified, 1):
        part["ref"] = f"S{index}"
    return verified, list(excluded.values()), len(candidates)
