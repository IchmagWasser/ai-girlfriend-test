from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from dotenv import load_dotenv
import os
import logging
import sqlite3
import hashlib
import csv
import io
import html
import hmac
import json
import re
from datetime import datetime
from time import time
from collections import defaultdict, deque
from typing import Dict, Any

from ollama_chat import get_response, get_response_with_messages
import hooks

# ──────────────────────────────
# Setup
# ──────────────────────────────
load_dotenv()
SECRET_KEY = os.getenv("SECRET_KEY", "supersecret")

app = FastAPI()
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY)

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("app")

DB_PATH = "users.db"
CHAT_TABLE_CREATED = False
RATE_LIMIT_FILE = "rate_limits.json"
MESSAGES_PER_HOUR = 50
SESSION_TIMEOUT_MINUTES = 30
SESSION_TIMEOUT_SECONDS = SESSION_TIMEOUT_MINUTES * 60

# Persona-Definitionen
PERSONAS = {
    "standard": {"name":"Standard Assistent","emoji":"🤖","system_prompt":"Du bist ein hilfsfreundlicher KI-Assistent. Antworte auf Deutsch und sei sachlich aber freundlich."},
    "freundlich":{"name":"Freundlicher Helfer","emoji":"😊","system_prompt":"Du bist ein sehr freundlicher und enthusiastischer Assistent. Verwende warme, ermutigende Worte und zeige echtes Interesse an den Fragen. Sei optimistisch und unterstützend."},
    "lustig":{"name":"Comedy Bot","emoji":"😄","system_prompt":"Du bist ein humorvoller Assistent der gerne Witze macht und lustige Antworten gibt. Bleibe trotzdem hilfreich, aber bringe den User zum Lächeln. Verwende gelegentlich Wortwitz oder lustige Vergleiche."},
    "professionell":{"name":"Business Experte","emoji":"👔","system_prompt":"Du bist ein professioneller Berater mit Expertise in Business und Technik. Antworte präzise, strukturiert und sachlich. Nutze Fachbegriffe angemessen und gib konkrete Handlungsempfehlungen."},
    "lehrerin":{"name":"Geduldige Lehrerin","emoji":"👩‍🏫","system_prompt":"Du bist eine geduldige Lehrerin die komplexe Themen einfach erklärt. Baue Erklärungen schrittweise auf, verwende Beispiele und frage nach ob alles verstanden wurde. Ermutige zum Lernen."},
    "kreativ":{"name":"Kreativer Geist","emoji":"🎨","system_prompt":"Du bist ein kreativer Assistent voller Ideen und Inspiration. Denke um die Ecke, schlage ungewöhnliche Lösungen vor und bringe künstlerische Perspektiven ein. Sei experimentierfreudig."}
}

# ──────────────────────────────
# Performance Monitoring Middleware
# ──────────────────────────────
import time as _pytime

class PerformanceMonitoringMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, slow_threshold: float = 2.0):
        super().__init__(app)
        self.slow_threshold = slow_threshold
        self.stats = {
            'total_requests': 0,
            'total_time': 0.0,
            'slow_requests': deque(maxlen=100),
            'endpoint_stats': defaultdict(lambda: {'count': 0, 'total_time': 0.0}),
            'last_requests': deque(maxlen=50)
        }
        try:
            app.state.performance_monitor = self
        except Exception:
            pass

    async def dispatch(self, request: Request, call_next):
        start = _pytime.perf_counter()
        response = await call_next(request)
        dur = _pytime.perf_counter() - start
        self._update_stats(request, dur)
        response.headers["X-Process-Time"] = str(round(dur, 4))
        return response

    def _update_stats(self, request: Request, dur: float):
        endpoint = f"{request.method} {request.url.path}"
        self.stats['total_requests'] += 1
        self.stats['total_time'] += dur
        es = self.stats['endpoint_stats'][endpoint]
        es['count'] += 1
        es['total_time'] += dur
        if dur > self.slow_threshold:
            self.stats['slow_requests'].append({
                'timestamp': _pytime.time(),
                'endpoint': endpoint,
                'duration': round(dur, 4),
                'user_agent': request.headers.get('user-agent', 'Unknown')[:120]
            })
        self.stats['last_requests'].append({
            'timestamp': _pytime.time(),
            'endpoint': endpoint,
            'duration': round(dur, 4),
            'status': 'slow' if dur > self.slow_threshold else 'normal'
        })

    def get_stats(self) -> Dict[str, Any]:
        avg = (self.stats['total_time'] / self.stats['total_requests']) if self.stats['total_requests'] else 0.0
        endpoint_stats = []
        for ep, data in self.stats['endpoint_stats'].items():
            avg_ep = (data['total_time'] / data['count']) if data['count'] else 0.0
            endpoint_stats.append({'endpoint': ep,'count': data['count'],'avg_time': round(avg_ep, 4),'total_time': round(data['total_time'], 4)})
        endpoint_stats.sort(key=lambda x: x['avg_time'], reverse=True)
        return {
            'total_requests': self.stats['total_requests'],
            'average_response_time': round(avg, 4),
            'total_time': round(self.stats['total_time'], 4),
            'slow_requests_count': len(self.stats['slow_requests']),
            'slow_threshold': self.slow_threshold,
            'endpoint_stats': endpoint_stats[:10],
            'recent_slow_requests': list(self.stats['slow_requests'])[-10:],
            'recent_requests': list(self.stats['last_requests'])[-20:]
        }

app.add_middleware(PerformanceMonitoringMiddleware, slow_threshold=2.0)

# ──────────────────────────────
# DB Helpers
# ──────────────────────────────
def init_db():
    global CHAT_TABLE_CREATED
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            username TEXT PRIMARY KEY,
            password TEXT NOT NULL,
            question TEXT NOT NULL,
            answer TEXT NOT NULL,
            is_admin INTEGER DEFAULT 0,
            is_blocked INTEGER DEFAULT 0,
            persona TEXT DEFAULT 'standard',
            subscription TEXT DEFAULT 'free'
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS chat_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (username) REFERENCES users (username)
        )
    """)

    # MIGRATION: subscription-Spalte nachziehen falls alte DB
    cursor.execute("PRAGMA table_info(users)")
    cols = [c[1] for c in cursor.fetchall()]
    if 'subscription' not in cols:
        cursor.execute("ALTER TABLE users ADD COLUMN subscription TEXT DEFAULT 'free'")

    # Default-Admin
    cursor.execute("SELECT username FROM users WHERE username = ?", ("admin",))
    if not cursor.fetchone():
        admin_hash = hash_password("admin")
        cursor.execute("""
            INSERT INTO users (username, password, question, answer, is_admin, subscription)
            VALUES (?, ?, ?, ?, 1, 'enterprise')
        """, ("admin", admin_hash, "Default Admin Question", "admin"))
        logger.info("[INIT] Admin-User erstellt (admin/admin)")

    conn.commit()
    conn.close()
    CHAT_TABLE_CREATED = True
    logger.info("[INIT] Datenbank initialisiert")

def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()

def render_markdown_simple(text: str) -> str:
    text = html.escape(text)
    text = re.sub(r'```(\w+)?\n?(.*?)```', r'<div class="code-block"><div class="code-header">\1</div><pre><code>\2</code></pre></div>', text, flags=re.DOTALL)
    text = re.sub(r'`([^`]+)`', r'<code class="inline-code">\1</code>', text)
    text = re.sub(r'\*\*([^*]+)\*\*', r'<strong>\1</strong>', text)
    text = re.sub(r'\*([^*]+)\*', r'<em>\1</em>', text)
    lines = text.split('\n')
    in_list = False
    result = []
    for line in lines:
        if line.strip().startswith('- '):
            if not in_list:
                result.append('<ul class="chat-list">')
                in_list = True
            result.append(f'<li>{line.strip()[2:]}</li>')
        else:
            if in_list:
                result.append('</ul>')
                in_list = False
            result.append(line)
    if in_list: result.append('</ul>')
    text = '\n'.join(result)
    text = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'<a href="\2" target="_blank" class="chat-link">\1</a>', text)
    return text.replace('\n', '<br>')

def get_user(username: str) -> dict:
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE username = ?", (username,))
    row = cursor.fetchone()
    conn.close()
    if row:
        return {
            "username": row[0],
            "password": row[1],
            "question": row[2],
            "answer": row[3],
            "is_admin": bool(row[4]),
            "is_blocked": bool(row[5]),
            "persona": row[6] if len(row) > 6 else "standard",
            "subscription": row[7] if len(row) > 7 else "free"
        }
    return None

def get_all_users() -> dict:
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT username, password, question, answer, is_admin, is_blocked, COALESCE(subscription,'free') FROM users")
    rows = cursor.fetchall()
    conn.close()
    users = {}
    for row in rows:
        users[row[0]] = {
            "password": row[1],
            "question": row[2],
            "answer": row[3],
            "is_admin": bool(row[4]),
            "blocked": bool(row[5]),
            "subscription": row[6] or "free"
        }
    return users

def set_user_subscription(username: str, sub: str):
    if sub not in ("free", "premium", "enterprise"):
        sub = "free"
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET subscription = ? WHERE username = ?", (sub, username))
    conn.commit()
    conn.close()

def save_user(username: str, password: str, question: str, answer: str) -> bool:
    if get_user(username):
        return False
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    try:
        password_hash = hash_password(password)
        cursor.execute("INSERT INTO users (username, password, question, answer) VALUES (?, ?, ?, ?)",
                       (username, password_hash, question, answer))
        conn.commit()
        logger.info(f"[REGISTER] Neuer User: {username}")
        try: hooks.on_user_register(username)
        except Exception: pass
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        conn.close()

def check_login(username: str, password: str) -> bool:
    user = get_user(username)
    return bool(user and user["password"] == hash_password(password))

def verify_security_answer(username: str, answer: str) -> bool:
    user = get_user(username)
    return bool(user and user["answer"] == answer)

def reset_password(username: str, new_password: str):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    password_hash = hash_password(new_password)
    cursor.execute("UPDATE users SET password = ? WHERE username = ?", (password_hash, username))
    conn.commit()
    conn.close()

def get_user_history(username: str) -> list:
    if not CHAT_TABLE_CREATED:
        return []
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT role, content, timestamp FROM chat_history WHERE username = ? ORDER BY timestamp ASC", (username,))
    rows = cursor.fetchall()
    conn.close()
    return [{"role": r[0], "content": r[1], "timestamp": r[2]} for r in rows]

def save_user_history(username: str, role: str, content: str):
    if not CHAT_TABLE_CREATED:
        return
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO chat_history (username, role, content) VALUES (?, ?, ?)", (username, role, content))
    conn.commit()
    conn.close()

def delete_user_history(username: str):
    if not CHAT_TABLE_CREATED:
        return
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM chat_history WHERE username = ?", (username,))
    conn.commit()
    conn.close()

def toggle_user_block(username: str):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT is_blocked FROM users WHERE username = ?", (username,))
    current = cursor.fetchone()[0]
    new_status = 0 if current else 1
    cursor.execute("UPDATE users SET is_blocked = ? WHERE username = ?", (new_status, username))
    conn.commit()
    conn.close()

def delete_user_completely(username: str):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM users WHERE username = ?", (username,))
    cursor.execute("DELETE FROM chat_history WHERE username = ?", (username,))
    conn.commit()
    conn.close()

def get_user_persona(username: str) -> str:
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT persona FROM users WHERE username = ?", (username,))
        result = cursor.fetchone()
        return result[0] if result and result[0] else "standard"
    except sqlite3.OperationalError:
        return "standard"
    finally:
        conn.close()

def save_user_persona(username: str, persona: str):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("PRAGMA table_info(users)")
    columns = [c[1] for c in cursor.fetchall()]
    if 'persona' not in columns:
        cursor.execute("ALTER TABLE users ADD COLUMN persona TEXT DEFAULT 'standard'")
    cursor.execute("UPDATE users SET persona = ? WHERE username = ?", (persona, username))
    conn.commit()
    conn.close()

def get_response_with_context(current_message: str, chat_history: list, persona: str = "standard") -> str:
    messages = []
    persona_config = PERSONAS.get(persona, PERSONAS["standard"])
    messages.append({"role": "system", "content": persona_config["system_prompt"]})
    for msg in chat_history[-20:]:
        if msg["role"] in ["user", "assistant"]:
            messages.append({"role": msg["role"], "content": msg["content"]})
    messages.append({"role": "user", "content": current_message})
    return get_response_with_messages(messages)

# ──────────────────────────────
# Rate-Limit & Session
# ──────────────────────────────
def load_rate_limits():
    if os.path.exists(RATE_LIMIT_FILE):
        try:
            with open(RATE_LIMIT_FILE, 'r') as f:
                return json.load(f)
        except:
            return {}
    return {}

def save_rate_limits(limits):
    with open(RATE_LIMIT_FILE, 'w') as f:
        json.dump(limits, f)

def check_rate_limit(username: str) -> bool:
    limits = load_rate_limits()
    now = time()
    user_data = limits.get(username, {"messages": [], "last_reset": now})
    hour_ago = now - 3600
    user_data["messages"] = [t for t in user_data["messages"] if t > hour_ago]
    if len(user_data["messages"]) >= MESSAGES_PER_HOUR:
        return False
    user_data["messages"].append(now)
    limits[username] = user_data
    save_rate_limits(limits)
    return True

def update_session_activity(request: Request):
    request.session["last_activity"] = time()

def check_session_timeout(request: Request) -> bool:
    last = request.session.get("last_activity")
    return True if not last else (time() - last) > SESSION_TIMEOUT_SECONDS

def require_active_session(request: Request):
    username = request.session.get("username")
    if not username:
        try: hooks.on_session_checked(None, False)
        except Exception: pass
        return RedirectResponse("/", status_code=302)
    if check_session_timeout(request):
        request.session.clear()
        try: hooks.on_session_checked(username, False)
        except Exception: pass
        return RedirectResponse("/?timeout=1", status_code=302)
    update_session_activity(request)
    try: hooks.on_session_checked(username, True)
    except Exception: pass
    return None

def is_admin(request: Request) -> bool:
    username = request.session.get("username")
    if username:
        user = get_user(username)
        return bool(user and user["is_admin"])
    return False

def admin_redirect_guard(request: Request):
    if not is_admin(request):
        return RedirectResponse("/", status_code=302)
    return None

# ──────────────────────────────
# Early request hook
# ──────────────────────────────
@app.middleware("http")
async def before_request_hook(request: Request, call_next):
    try:
        scope = {"type": request.scope.get("type"), "path": request.url.path, "method": request.method}
        hooks.on_before_request(scope)
    except Exception:
        pass
    return await call_next(request)

# ──────────────────────────────
# Routes - Auth
# ──────────────────────────────
@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    request.session.clear()
    return templates.TemplateResponse("login.html", {"request": request})

@app.post("/login", response_class=HTMLResponse)
async def login(request: Request, username: str = Form(...), password: str = Form(...)):
    if check_login(username, password):
        user = get_user(username)
        if user["is_blocked"]:
            return templates.TemplateResponse("login.html", {"request": request, "error": "Du wurdest vom Admin gesperrt."})
        request.session["username"] = username
        update_session_activity(request)
        try: hooks.on_login_success(username)
        except Exception: pass
        return RedirectResponse("/admin" if user["is_admin"] else "/chat", status_code=302)
    try: hooks.on_login_failure(username)
    except Exception: pass
    return templates.TemplateResponse("login.html", {"request": request, "error": "Falsche Anmeldedaten"})

@app.get("/register", response_class=HTMLResponse)
async def register_page(request: Request):
    return templates.TemplateResponse("register.html", {"request": request})

@app.post("/register", response_class=HTMLResponse)
async def register(request: Request,
                   username: str = Form(...),
                   password: str = Form(...),
                   question: str = Form(...),
                   answer: str = Form(...)):
    if save_user(username, password, question, answer):
        return RedirectResponse("/", status_code=302)
    return templates.TemplateResponse("register.html", {"request": request, "error": "Benutzer existiert bereits"})

@app.get("/reset", response_class=HTMLResponse)
async def reset_page(request: Request):
    return templates.TemplateResponse("reset.html", {"request": request, "error": "", "success": ""})

@app.post("/reset", response_class=HTMLResponse)
async def reset_post(request: Request,
                     username: str = Form(...),
                     answer: str = Form(...),
                     new_password: str = Form(...)):
    if verify_security_answer(username, answer):
        reset_password(username, new_password)
        try: hooks.on_password_reset(username)
        except Exception: pass
        return RedirectResponse("/", status_code=302)
    return templates.TemplateResponse("reset.html", {"request": request, "error": "Antwort falsch", "success": ""})

@app.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/", status_code=302)

# ──────────────────────────────
# Routes - Chat
# ──────────────────────────────
@app.get("/chat", response_class=HTMLResponse)
async def chat_page(request: Request):
    redirect = require_active_session(request)
    if redirect: return redirect
    username = request.session.get("username")
    history = get_user_history(username)
    return templates.TemplateResponse("chat.html", {
        "request": request,
        "username": username,
        "chat_history": history,
        "session_timeout_minutes": SESSION_TIMEOUT_MINUTES
    })

@app.post("/chat")
async def chat_with_markdown(req: Request):
    redirect = require_active_session(req)
    if redirect:
        return {"reply": "Session abgelaufen. Bitte neu anmelden.", "redirect": "/"}
    username = req.session.get("username")
    if not check_rate_limit(username):
        return {"reply": f"Rate-Limit erreicht! Maximal {MESSAGES_PER_HOUR} Nachrichten pro Stunde."}
    data = await req.json()
    user_message = data.get("message", "")
    if not user_message.strip():
        return {"reply": "Leere Nachricht."}
    try:
        user_message = hooks.on_user_message(username, user_message)
    except Exception:
        pass
    save_user_history(username, "user", user_message)
    history = get_user_history(username)
    user_persona = get_user_persona(username)
    try:
        try:
            persona_config = PERSONAS.get(user_persona, PERSONAS["standard"])
            messages = [{"role": "system", "content": persona_config["system_prompt"]}]
            for msg in history[-20:]:
                if msg["role"] in ["user", "assistant"]:
                    messages.append({"role": msg["role"], "content": msg["content"]})
            messages.append({"role": "user", "content": user_message})
            hooks.on_before_llm_request(username, messages)
        except Exception:
            pass
        raw_response = get_response_with_context(user_message, history, user_persona)
        try:
            raw_response = hooks.on_assistant_response(username, raw_response)
        except Exception:
            pass
        rendered = render_markdown_simple(raw_response)
        save_user_history(username, "assistant", raw_response)
        return {"reply": rendered, "raw_reply": raw_response}
    except Exception as e:
        logger.error(f"Chat error for {username}: {str(e)}")
        return {"reply": "Ein Fehler ist aufgetreten. Versuche es erneut."}

@app.post("/chat/clear-history")
async def clear_user_history(request: Request):
    redirect = require_active_session(request)
    if redirect: return redirect
    username = request.session.get("username")
    delete_user_history(username)
    logger.info(f"[USER] {username} hat seinen Chat-Verlauf gelöscht")
    return RedirectResponse("/chat", status_code=302)

# ──────────────────────────────
# Routes - Persona
# ──────────────────────────────
@app.get("/persona", response_class=HTMLResponse)
async def persona_settings(request: Request):
    redirect = require_active_session(request)
    if redirect: return redirect
    username = request.session.get("username")
    current_persona = get_user_persona(username)
    return templates.TemplateResponse("persona.html", {
        "request": request,
        "username": username,
        "personas": PERSONAS,
        "current_persona": current_persona
    })

@app.post("/persona")
async def set_persona(request: Request, persona: str = Form(...)):
    redirect = require_active_session(request)
    if redirect: return redirect
    username = request.session.get("username")
    if persona in PERSONAS:
        save_user_persona(username, persona)
        logger.info(f"[PERSONA] {username} wählte Persona: {persona}")
    return RedirectResponse("/chat", status_code=302)

# ──────────────────────────────
# Routes - Admin
# ──────────────────────────────
@app.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request):
    guard = admin_redirect_guard(request)
    if guard: return guard
    redirect = require_active_session(request)
    if redirect: return redirect
    users = get_all_users()

    # Stats für dein Template
    total_users = len(users)
    blocked_users = sum(1 for u in users.values() if u["blocked"])
    premium_users = sum(1 for u in users.values() if (u.get("subscription") == "premium" or u.get("subscription") == "enterprise"))
    free_users = total_users - premium_users
    stats = {
        "total_users": total_users,
        "blocked_users": blocked_users,
        "premium_users": premium_users,
        "free_users": free_users
    }

    return templates.TemplateResponse("admin_users.html", {"request": request, "users": users, "stats": stats})

@app.post("/admin/set-subscription")
async def admin_set_subscription(request: Request,
                                 username: str = Form(...),
                                 subscription: str = Form(...)):
    guard = admin_redirect_guard(request)
    if guard: return guard
    if username != "admin":
        set_user_subscription(username, subscription)
        try:
            actor = request.session.get("username") or "admin"
            hooks.on_admin_action(actor, "set_subscription", f"{username}:{subscription}")
        except Exception:
            pass
    return RedirectResponse("/admin", status_code=302)

@app.post("/admin/toggle-block")
async def toggle_block_user(request: Request, username: str = Form(...)):
    guard = admin_redirect_guard(request)
    if guard: return guard
    if username != "admin":
        toggle_user_block(username)
        logger.info(f"[ADMIN] User blockiert/freigeschaltet: {username}")
        try:
            actor = request.session.get("username") or "admin"
            hooks.on_admin_action(actor, "toggle_block", username)
        except Exception:
            pass
    return RedirectResponse("/admin", status_code=302)

@app.get("/admin/history/{username}", response_class=HTMLResponse)
async def view_user_history(request: Request, username: str):
    guard = admin_redirect_guard(request)
    if guard: return guard
    history = get_user_history(username)
    formatted = "<br><br>".join(
        f"<b>{html.escape(str(msg.get('role', 'unknown')))}:</b><br>{html.escape(str(msg.get('content', '')))}"
        for msg in history
    )
    body = f"""
    <html><body style="font-family: Arial; padding: 20px;">
    <h1>Chat-Verlauf von {html.escape(username)}</h1>
    <div style="background: #f5f5f5; padding: 15px; border-radius: 5px; margin: 20px 0;">
        {formatted or '— Kein Verlauf vorhanden —'}
    </div>
    <a href='/admin' style="color: blue;">🔙 Zurück zum Admin-Panel</a>
    </body></html>
    """
    return HTMLResponse(body)

@app.post("/admin/delete-history")
async def delete_history(request: Request, username: str = Form(...)):
    guard = admin_redirect_guard(request)
    if guard: return guard
    delete_user_history(username)
    logger.info(f"[ADMIN] Chat-Verlauf gelöscht: {username}")
    try:
        actor = request.session.get("username") or "admin"
        hooks.on_admin_action(actor, "delete_history", username)
    except Exception:
        pass
    return RedirectResponse("/admin", status_code=302)

@app.post("/admin/delete-user")
async def delete_user(request: Request, username: str = Form(...)):
    guard = admin_redirect_guard(request)
    if guard: return guard
    if username != "admin":
        delete_user_completely(username)
        logger.info(f"[ADMIN] User gelöscht: {username}")
        try:
            actor = request.session.get("username") or "admin"
            hooks.on_admin_action(actor, "delete_user", username)
        except Exception:
            pass
    return RedirectResponse("/admin", status_code=302)

@app.post("/admin/change-user-password")
async def change_user_password(request: Request,
                               username: str = Form(...),
                               new_password: str = Form(...)):
    guard = admin_redirect_guard(request)
    if guard: return guard
    if username != "admin":
        reset_password(username, new_password)
        logger.info(f"[ADMIN] Passwort geändert für: {username}")
        try:
            actor = request.session.get("username") or "admin"
            hooks.on_admin_action(actor, "change_pw", username)
            hooks.on_password_reset(username)
        except Exception:
            pass
    return RedirectResponse("/admin", status_code=302)

@app.post("/admin/change-password")
async def change_admin_password(request: Request,
                                old_password: str = Form(...),
                                new_password: str = Form(...)):
    guard = admin_redirect_guard(request)
    if guard: return guard
    admin_user = get_user("admin")
    if admin_user and hmac.compare_digest(admin_user["password"], hash_password(old_password)):
        reset_password("admin", new_password)
        logger.info("[ADMIN] Admin-Passwort geändert")
        try:
            actor = request.session.get("username") or "admin"
            hooks.on_admin_action(actor, "change_pw", "admin")
            hooks.on_password_reset("admin")
        except Exception:
            pass
        return RedirectResponse("/admin", status_code=302)
    else:
        users = get_all_users()
        total_users = len(users)
        blocked_users = sum(1 for u in users.values() if u["blocked"])
        premium_users = sum(1 for u in users.values() if (u.get("subscription") == "premium" or u.get("subscription") == "enterprise"))
        free_users = total_users - premium_users
        stats = {"total_users": total_users, "blocked_users": blocked_users, "premium_users": premium_users, "free_users": free_users}
        return templates.TemplateResponse("admin_users.html", {"request": request, "users": users, "error": "Altes Passwort ist falsch", "stats": stats})

@app.get("/admin/export-csv")
async def export_csv():
    users = get_all_users()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Benutzername", "Passwort-Hash", "Frage", "Antwort", "Admin", "Blockiert", "Subscription"])
    for name, data in users.items():
        writer.writerow([
            name,
            data.get("password", ""),
            data.get("question", ""),
            data.get("answer", ""),
            "Ja" if data.get("is_admin") else "Nein",
            "Ja" if data.get("blocked") else "Nein",
            data.get("subscription", "free")
        ])
    output.seek(0)
    return StreamingResponse(io.BytesIO(output.getvalue().encode("utf-8")),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=users.csv"}
    )

# ──────────────────────────────
# Admin: Performance Monitoring
# ──────────────────────────────
@app.get("/admin/performance", response_class=HTMLResponse)
async def admin_performance(request: Request):
    guard = admin_redirect_guard(request)
    if guard: return guard
    redirect = require_active_session(request)
    if redirect: return redirect
    monitor = getattr(app.state, "performance_monitor", None)
    stats = monitor.get_stats() if monitor else {
        "total_requests": 0, "average_response_time": 0.0, "total_time": 0.0,
        "slow_requests_count": 0, "slow_threshold": 2.0,
        "endpoint_stats": [], "recent_slow_requests": [], "recent_requests": []
    }
    return templates.TemplateResponse("performance.html", {"request": request, "stats": stats})

@app.get("/api/performance-stats")
async def api_performance_stats(request: Request):
    guard = admin_redirect_guard(request)
    if guard: return {"error": "Unauthorized"}
    redirect = require_active_session(request)
    if redirect: return {"error": "Session expired"}
    monitor = getattr(app.state, "performance_monitor", None)
    return monitor.get_stats() if monitor else {"error": "monitor_unavailable"}

# ──────────────────────────────
# API
# ──────────────────────────────
@app.get("/api/session-info")
async def session_info(request: Request):
    if not request.session.get("username"):
        return {"active": False}
    last = request.session.get("last_activity", time())
    remaining = max(0, SESSION_TIMEOUT_SECONDS - (time() - last))
    return {"active": True,"username": request.session.get("username"),"remaining_minutes": int(remaining / 60),"remaining_seconds": int(remaining)}

# ──────────────────────────────
# Startup
# ──────────────────────────────
@app.on_event("startup")
def startup():
    init_db()
    logger.info("[STARTUP] KI-Chat gestartet")
