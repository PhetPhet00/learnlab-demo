#!/usr/bin/env python3
"""LearnLab demo server: Python standard library + SQLite."""
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs
import sqlite3, json, os, re
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "learnlab.db"
DEFAULT_STATE = {
    "profile": {"name": "นักเรียน", "age": "", "level": "มัธยมปลาย", "goal": "20"},
    "progress": {"math": 65, "english": 42, "science": 80},
    "completed": [], "history": [], "lastScore": None
}

def connect():
    db = sqlite3.connect(DB_PATH, timeout=10)
    db.row_factory = sqlite3.Row
    return db

def init_db():
    with connect() as db:
        db.executescript('''
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS learner_state (
            client_id TEXT PRIMARY KEY,
            state_json TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS learners (
            client_id TEXT PRIMARY KEY,
            name TEXT NOT NULL DEFAULT 'นักเรียน',
            age TEXT NOT NULL DEFAULT '',
            education TEXT NOT NULL DEFAULT 'มัธยมปลาย',
            daily_goal INTEGER NOT NULL DEFAULT 20,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS course_progress (
            client_id TEXT NOT NULL,
            course_id TEXT NOT NULL,
            percent INTEGER NOT NULL DEFAULT 0 CHECK(percent >= 0 AND percent <= 100),
            updated_at TEXT NOT NULL,
            PRIMARY KEY (client_id, course_id)
        );
        CREATE TABLE IF NOT EXISTS completed_lessons (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id TEXT NOT NULL,
            title TEXT NOT NULL,
            completed_at TEXT NOT NULL,
            UNIQUE(client_id, title)
        );
        CREATE TABLE IF NOT EXISTS quiz_attempts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id TEXT NOT NULL,
            score INTEGER NOT NULL CHECK(score >= 0 AND score <= 100),
            correct_answers INTEGER NOT NULL,
            total_questions INTEGER NOT NULL,
            attempted_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_quiz_attempts_client ON quiz_attempts(client_id, id DESC);
        ''')

def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

def valid_client_id(value):
    return isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_-]{8,80}", value) is not None

class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def log_message(self, fmt, *args):
        print("[%s] %s" % (self.log_date_time_string(), fmt % args))

    def send_json(self, code, data):
        raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/health":
            return self.send_json(200, {"ok": True, "database": "SQLite", "file": DB_PATH.name})
        if parsed.path == "/api/state":
            client_id = parse_qs(parsed.query).get("client_id", [""])[0]
            if not valid_client_id(client_id):
                return self.send_json(400, {"error": "client_id ไม่ถูกต้อง"})
            with connect() as db:
                row = db.execute("SELECT state_json FROM learner_state WHERE client_id=?", (client_id,)).fetchone()
            state = json.loads(row["state_json"]) if row else DEFAULT_STATE
            return self.send_json(200, {"state": state, "stored": row is not None})
        return super().do_GET()

    def do_POST(self):
        if urlparse(self.path).path != "/api/state":
            return self.send_json(404, {"error": "ไม่พบ endpoint"})
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 1_000_000:
                return self.send_json(413, {"error": "ขนาดข้อมูลไม่ถูกต้อง"})
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            client_id, state = payload.get("client_id"), payload.get("state")
            if not valid_client_id(client_id) or not isinstance(state, dict):
                return self.send_json(400, {"error": "ข้อมูลไม่ถูกต้อง"})
            profile = state.get("profile") or {}
            progress = state.get("progress") or {}
            completed = state.get("completed") or []
            history = state.get("history") or []
            if not isinstance(profile, dict) or not isinstance(progress, dict) or not isinstance(completed, list) or not isinstance(history, list):
                return self.send_json(400, {"error": "รูปแบบข้อมูลไม่ถูกต้อง"})
            stamp = now()
            clean = {
                "profile": {
                    "name": str(profile.get("name", "นักเรียน"))[:50] or "นักเรียน",
                    "age": str(profile.get("age", ""))[:3],
                    "level": str(profile.get("level", "มัธยมปลาย"))[:40],
                    "goal": str(profile.get("goal", "20"))[:3]
                },
                "progress": {k: max(0, min(100, int(v))) for k, v in progress.items() if k in {"math", "english", "science"}},
                "completed": [str(x)[:200] for x in completed[:300] if isinstance(x, (str, int, float))],
                "history": [],
                "lastScore": state.get("lastScore") if isinstance(state.get("lastScore"), int) and 0 <= state.get("lastScore") <= 100 else None
            }
            for item in history[:100]:
                if isinstance(item, dict):
                    try:
                        score = max(0, min(100, int(item.get("score", 0))))
                        correct = max(0, int(item.get("correct", 0)))
                        total = max(1, int(item.get("total", 1)))
                        clean["history"].append({"date": str(item.get("date", ""))[:40], "score": score, "correct": correct, "total": total})
                    except (ValueError, TypeError):
                        continue
            with connect() as db:
                db.execute("INSERT INTO learner_state(client_id,state_json,updated_at) VALUES(?,?,?) ON CONFLICT(client_id) DO UPDATE SET state_json=excluded.state_json,updated_at=excluded.updated_at", (client_id, json.dumps(clean, ensure_ascii=False), stamp))
                db.execute("INSERT INTO learners(client_id,name,age,education,daily_goal,updated_at) VALUES(?,?,?,?,?,?) ON CONFLICT(client_id) DO UPDATE SET name=excluded.name,age=excluded.age,education=excluded.education,daily_goal=excluded.daily_goal,updated_at=excluded.updated_at", (client_id, clean["profile"]["name"], clean["profile"]["age"], clean["profile"]["level"], int(clean["profile"]["goal"]) if clean["profile"]["goal"].isdigit() else 20, stamp))
                db.execute("DELETE FROM course_progress WHERE client_id=?", (client_id,))
                for course_id, percent in clean["progress"].items():
                    db.execute("INSERT INTO course_progress(client_id,course_id,percent,updated_at) VALUES(?,?,?,?)", (client_id, course_id, percent, stamp))
                db.execute("DELETE FROM completed_lessons WHERE client_id=?", (client_id,))
                for title in clean["completed"]:
                    db.execute("INSERT OR IGNORE INTO completed_lessons(client_id,title,completed_at) VALUES(?,?,?)", (client_id, title, stamp))
                # Rebuild attempts from the snapshot for this demo's per-browser history.
                db.execute("DELETE FROM quiz_attempts WHERE client_id=?", (client_id,))
                for item in reversed(clean["history"]):
                    db.execute("INSERT INTO quiz_attempts(client_id,score,correct_answers,total_questions,attempted_at) VALUES(?,?,?,?,?)", (client_id, item["score"], item["correct"], item["total"], stamp))
            return self.send_json(200, {"ok": True, "saved": True, "updated_at": stamp})
        except (json.JSONDecodeError, UnicodeDecodeError):
            return self.send_json(400, {"error": "JSON ไม่ถูกต้อง"})
        except Exception as exc:
            print("API error:", repr(exc))
            return self.send_json(500, {"error": "เกิดข้อผิดพลาดในการบันทึกข้อมูล"})

if __name__ == "__main__":
    init_db()
    host = os.environ.get("LEARNLAB_HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8000"))
    print(f"LearnLab running at http://{host}:{port}")
    print(f"SQLite database: {DB_PATH}")
    ThreadingHTTPServer((host, port), Handler).serve_forever()
