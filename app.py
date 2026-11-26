"""Serve the local AgentShield chat and its streamed activity events."""

import argparse
import base64
import binascii
import json
import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from gateway import MODEL, live_enabled, run_turn, screen_document
import email_agent
import storage


STATIC = Path(__file__).resolve().parent / "web"
ASSETS = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
}


class Dashboard(BaseHTTPRequestHandler):
    def send_response_headers(self, status, content_type, length=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        if length is not None:
            self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy",
                         "default-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        self.end_headers()

    def respond(self, status, value):
        body = json.dumps(value).encode("utf-8")
        self.send_response_headers(status, "application/json", len(body))
        self.wfile.write(body)

    def local_request(self):
        hosts = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
        origin = self.headers.get("Origin")
        return self.headers.get("Host") in hosts and (not origin or origin in {f"http://{host}" for host in hosts})

    def do_GET(self):
        if not self.local_request():
            self.respond(403, {"error": "Open the app at its localhost address."})
            return
        if self.path in ASSETS:
            name, content_type = ASSETS[self.path]
            body = (STATIC / name).read_bytes()
            self.send_response_headers(200, content_type, len(body))
            self.wfile.write(body)
        elif self.path == "/api/email/config":
            self.respond(200, email_agent.account_status())
        elif self.path == "/api/config":
            self.respond(200, {"model": MODEL, "live": live_enabled()})
        elif self.path == "/api/conversations":
            self.respond(200, storage.conversations())
        elif match := re.fullmatch(r"/api/conversations/([a-f0-9]{32})", self.path):
            thread = storage.load_conversation(match[1])
            self.respond(200, thread) if thread else self.respond(404, {"error": "Conversation not found."})
        elif self.path == "/api/documents":
            self.respond(200, storage.documents())
        elif match := re.fullmatch(r"/api/documents/([a-f0-9]{32})", self.path):
            source = storage.source(match[1])
            self.respond(200, source) if source else self.respond(404, {"error": "Source not found."})
        elif self.path == "/favicon.ico":
            self.send_response_headers(204, "image/x-icon", 0)
        else:
            self.respond(404, {"error": "Unknown route."})

    def do_POST(self):
        email_action = {
            "/api/email/draft": email_agent.generate_draft,
            "/api/email/review": email_agent.review,
            "/api/email/send": email_agent.send,
            "/api/email/status": email_agent.delivery_status,
            "/api/email/gmail": email_agent.connect_gmail,
            "/api/email/gmail/disconnect": email_agent.disconnect_gmail,
        }.get(self.path)
        document_action = re.fullmatch(r"/api/documents/([a-f0-9]{32})/state", self.path)
        if self.path not in ("/api/chat", "/api/documents") and not document_action and not email_action:
            self.respond(404, {"error": "Unknown route."})
            return
        if not self.local_request():
            self.respond(403, {"error": "Open the app at its localhost address."})
            return
        if self.headers.get_content_type() != "application/json":
            self.respond(415, {"error": "Send a JSON request."})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            limit = 2_700_000 if self.path == "/api/documents" else 32_000
            if not 0 < length <= limit:
                raise ValueError
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise ValueError
        except ValueError:
            self.respond(400, {"error": "The request body is invalid or exceeds its size limit."})
            return
        if email_action:
            try:
                self.respond(200, email_action(data))
            except email_agent.EmailProblem as failure:
                self.respond(failure.status, {
                    "error": str(failure), "checks": failure.checks,
                })
        elif self.path == "/api/documents":
            self.upload(data)
        elif document_action:
            try:
                found = storage.set_document_state(document_action[1], data.get("state"), screen_document)
                self.respond(200 if found else 404, {"saved": True} if found else {"error": "Source not found."})
            except ValueError as failure:
                self.respond(400, {"error": str(failure)})
        else:
            self.chat(data)

    def upload(self, data):
        name, encoded = data.get("name"), data.get("content")
        if not isinstance(name, str) or not isinstance(encoded, str):
            self.respond(400, {"error": "Choose a file to upload."})
            return
        try:
            name.encode("utf-8")
            body = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError):
            self.respond(400, {"error": "The upload could not be decoded."})
            return
        try:
            identifier = storage.import_document(name, body, screen_document)
            self.respond(201, {"id": identifier})
        except ValueError as failure:
            self.respond(400, {"error": str(failure)})

    def chat(self, data):
        prompt, identifier, knowledge = (data.get(key) for key in ("prompt", "conversation_id", "knowledge"))
        source_id = data.get("source_id")
        try:
            if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 4000:
                raise ValueError
            prompt = prompt.strip()
            prompt.encode("utf-8")
            if not isinstance(knowledge, bool):
                raise ValueError
            if identifier is not None and (not isinstance(identifier, str) or not re.fullmatch(r"[a-f0-9]{32}", identifier)):
                raise ValueError
            if source_id is not None and (not isinstance(source_id, str) or not re.fullmatch(r"[a-f0-9]{32}", source_id)):
                raise ValueError
        except ValueError:
            self.respond(400, {"error": "Enter a message of 1 to 4000 characters."})
            return
        configured_key = os.getenv("NVIDIA_API_KEY", "").strip()
        if configured_key and configured_key in prompt:
            self.respond(400, {"error": "Remove the NVIDIA credential from the chat message."})
            return
        if email_agent.gmail_secret_present(prompt):
            self.respond(400, {"error": "Remove the Gmail app password from the chat message."})
            return
        if identifier is None:
            identifier = storage.create_conversation(prompt)
        elif storage.load_conversation(identifier) is None:
            self.respond(404, {"error": "Conversation not found. Start a new chat."})
            return
        history = storage.history(identifier)
        self.send_response_headers(200, "application/x-ndjson; charset=utf-8")

        def emit(value):
            self.wfile.write((json.dumps(value, ensure_ascii=False) + "\n").encode("utf-8"))
            self.wfile.flush()

        try:
            emit({"type": "conversation", "id": identifier})
            result = run_turn(prompt, history, knowledge or source_id is not None, emit, source_id)
            storage.save_turn(identifier, prompt, result)
            emit({"type": "complete", "result": result})
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            pass


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    storage.initialize()
    storage.backfill_source_audits(screen_document)
    with ThreadingHTTPServer(("127.0.0.1", args.port), Dashboard) as server:
        print(f"AgentShield Chat is listening at http://127.0.0.1:{server.server_port}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
