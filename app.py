# Alle Imports und Setup bleiben gleich...
from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from dotenv import load_dotenv
import os
import logging
import sqlite3
import hashlib
import csv
import io
import tempfile
import html
import hmac
import json
import re
import asyncio
from datetime import datetime
from starlette.middleware.base import BaseHTTPMiddleware
from typing import Dict, List
from collections import defaultdict, deque
import time as _pytime
import time
import threading
from functools import wraps, lru_cache

from ollama_chat import get_response, get_response_with_messages

# Connection Pool Imports
from database_pool import (
    DatabaseManager, 
    db_manager,
    get_user_pooled,
    get_all_users_pooled,
    save_user_pooled,
    get_database_performance_stats,
    with_db_connection
)

# Setup (bleibt gleich)
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

# Cache System (bleibt gleich)
class SimpleCache:
    def __init__(self, default_ttl: int = 300):
        self.cache = {}
        self.ttl_data = {}
        self.lock = threading.Lock()
        self.default_ttl = default_ttl
        
    def get(self, key: str):
        with self.lock:
            if key in self.cache:
                if time.time() < self.ttl_data.get(key, 0):
                    return self.cache[key]
                else:
                    self.cache.pop(key, None)
                    self.ttl_data.pop(key, None)
            return None
    
    def set(self, key: str, value, ttl: int = None):
        ttl = ttl or self.default_ttl
        expire_time = time.time() + ttl
        
        with self.lock:
            self.cache[key] = value
            self.ttl_data[key] = expire_time
    
    def delete(self, key: str):
        with self.lock:
            self.cache.pop(key, None)
            self.ttl_data.pop(key, None)
    
    def clear(self):
        with self.lock:
            self.cache.clear()
            self.ttl_data.clear()
    
    def cleanup_expired(self):
        current_time = time.time()
        expired_keys = []
        
        with self.lock:
            for key, expire_time in self.ttl_data.items():
                if current_time >= expire_time:
                    expired_keys.append(key)
            
            for key in expired_keys:
                self.cache.pop(key, None)
                self.ttl_data.pop(key, None)
        
        return len(expired_keys)
    
    def stats(self):
        with self.lock:
            return {
                "total_entries": len(self.cache),
                "memory_usage_mb": round(
                    sum(len(str(v)) for v in self.cache.values()) / 1024 / 1024, 2
                ),
                "cache_keys": list(self.cache.keys())
            }

app_cache = SimpleCache(default_ttl=300)

def cache_result(key_prefix: str, ttl: int = 300):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            cache_key = f"{key_prefix}:{':'.join(map(str, args))}"
            if kwargs:
                cache_key += f":{hash(frozenset(kwargs.items()))}"
            
            cached = app_cache.get(cache_key)
            if cached is not None:
                return cached
            
            result = func(*args, **kwargs)
            
            if result is not None:
                app_cache.set(cache_key, result, ttl)
            
            return result
        return wrapper
    return decorator

def invalidate_user_cache(username: str):
    keys_to_delete = [
        f"user:{username}",
        f"user_persona:{username}",
        f"user_tier:{username}",
        f"available_personas:{username}"
    ]
    
    for key in keys_to_delete:
        app_cache.delete(key)

# Subscription Tiers & Personas (bleiben gleich)
SUBSCRIPTION_TIERS = {
    "free": {
        "name": "Free",
        "max_messages_per_hour": 10,
        "max_messages_per_day": 50,
        "available_personas": ["standard", "freundlich"],
        "features": ["Basis-Chat", "2 Personas"]
    },
    "pro": {
        "name": "Pro", 
        "max_messages_per_hour": 100,
        "max_messages_per_day": 500,
        "available_personas": ["standard", "freundlich", "lustig", "professionell"],
        "features": ["Erweiterte Personas", "Mehr Nachrichten", "Priorität"]
    },
    "premium": {
        "name": "Premium",
        "max_messages_per_hour": -1,
        "max_messages_per_day": -1,
        "available_personas": ["standard", "freundlich", "lustig", "professionell", "lehrerin", "kreativ", "analyst", "therapeut"],
        "features": ["Alle Personas", "Unbegrenzte Nachrichten", "Höchste Priorität", "Erweiterte Features"]
    }
}

PERSONAS = {
    "standard": {
        "name": "Standard Assistent",
        "emoji": "🤖",
        "system_prompt": "Du bist ein hilfsfreundlicher KI-Assistent. Antworte auf Deutsch und sei sachlich aber freundlich.",
        "tier": "free",
        "description": "Der klassische KI-Assistent für alltägliche Fragen"
    },
    "freundlich": {
        "name": "Freundlicher Helfer", 
        "emoji": "😊",
        "system_prompt": "Du bist ein sehr freundlicher und enthusiastischer Assistent. Verwende warme, ermutigende Worte und zeige echtes Interesse an den Fragen. Sei optimistisch und unterstützend.",
        "tier": "free",
        "description": "Besonders warmherzig und ermutigend"
    },
    "lustig": {
        "name": "Comedy Bot",
        "emoji": "😄", 
        "system_prompt": "Du bist ein humorvoller Assistent der gerne Witze macht und lustige Antworten gibt. Bleibe trotzdem hilfreich, aber bringe den User zum Lächeln. Verwende gelegentlich Wortwitz oder lustige Vergleiche.",
        "tier": "pro",
        "description": "Bringt Humor in jede Unterhaltung"
    },
    "professionell": {
        "name": "Business Experte",
        "emoji": "👔",
        "system_prompt": "Du bist ein professioneller Berater mit Expertise in Business und Technik. Antworte präzise, strukturiert und sachlich. Nutze Fachbegriffe angemessen und gib konkrete Handlungsempfehlungen.",
        "tier": "pro",
        "description": "Für geschäftliche und technische Anfragen"
    },
    "lehrerin": {
        "name": "Geduldige Lehrerin", 
        "emoji": "👩‍🏫",
        "system_prompt": "Du bist eine geduldige Lehrerin die komplexe Themen einfach erklärt. Baue Erklärungen schrittweise auf, verwende Beispiele und frage nach ob alles verstanden wurde. Ermutige zum Lernen.",
        "tier": "premium",
        "description": "Erklärt komplexe Themen verständlich"
    },
    "kreativ": {
        "name": "Kreativer Geist",
        "emoji": "🎨", 
        "system_prompt": "Du bist ein kreativer Assistent voller Ideen und Inspiration. Denke um die Ecke, schlage ungewöhnliche Lösungen vor und bringe künstlerische Perspektiven ein. Sei experimentierfreudig.",
        "tier": "premium",
        "description": "Für kreative Projekte und Inspiration"
    },
    "analyst": {
        "name": "Daten Analyst",
        "emoji": "📊",
        "system_prompt": "Du bist ein präziser Datenanalyst. Analysiere Informationen systematisch, identifiziere Muster und Trends, und präsentiere Erkenntnisse klar strukturiert mit Zahlen und Fakten.",
        "tier": "premium", 
        "description": "Spezialist für Datenanalyse und Statistiken"
    },
    "therapeut": {
        "name": "Empathischer Berater",
        "emoji": "🧘‍♀️",
        "system_prompt": "Du bist ein einfühlsamer Gesprächspartner der aktiv zuhört und unterstützt. Stelle durchdachte Fragen, biete verschiedene Perspektiven und hilfe dabei, Gedanken zu ordnen. Sei verständnisvoll aber nicht direktiv.",
        "tier": "premium",
        "description": "Für persönliche Gespräche und Reflexion"
    }
}

# Performance Monitoring (bleibt gleich)
class PerformanceMonitoringMiddleware(BaseHTTPMiddleware):
    instance = None
    
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
        PerformanceMonitoringMiddleware.instance = self

    async def dispatch(self, request: Request, call_next):
        start_time = _pytime.time()
        response = await call_next(request)
        process_time = _pytime.time() - start_time
        self._update_stats(request, process_time)
        response.headers["X-Process-Time"] = str(round(process_time, 4))
        return response

    def _update_stats(self, request: Request, process_time: float):
        path = request.url.path
        method = request.method
        endpoint = f"{method} {path}"

        self.stats['total_requests'] += 1
        self.stats['total_time'] += process_time
        self.stats['endpoint_stats'][endpoint]['count'] += 1
        self.stats['endpoint_stats'][endpoint]['total_time'] += process_time

        if process_time > self.slow_threshold:
            self.stats['slow_requests'].append({
                'timestamp': _pytime.time(),
                'endpoint': endpoint,
                'duration': round(process_time, 4),
                'user_agent': request.headers.get('user-agent', 'Unknown')[:100]
            })

        self.stats['last_requests'].append({
            'timestamp': _pytime.time(),
            'endpoint': endpoint,
            'duration': round(process_time, 4),
            'status': 'slow' if process_time > self.slow_threshold else 'normal'
        })

    def get_stats(self) -> Dict:
        avg_time = (self.stats['total_time'] / self.stats['total_requests']
                   if self.stats['total_requests'] > 0 else 0)

        endpoint_stats = []
        for endpoint, data in self.stats['endpoint_stats'].items():
            avg_endpoint_time = data['total_time'] / data['count']
            endpoint_stats.append({
                'endpoint': endpoint,
                'count': data['count'],
                'avg_time': round(avg_endpoint_time, 4),
                'total_time': round(data['total_time'], 4)
            })

        endpoint_stats.sort(key=lambda x: x['avg_time'], reverse=True)

        return {
            'total_requests': self.stats['total_requests'],
            'average_response_time': round(avg_time, 4),
            'total_time': round(self.stats['total_time'], 4),
            'slow_requests_count': len(self.stats['slow_requests']),
            'slow_threshold': self.slow_threshold,
            'endpoint_stats': endpoint_stats[:10],
            'recent_slow_requests': list(self.stats['slow_requests'])[-10:],
            'recent_requests': list(self.stats['last_requests'])[-20:]
        }

app.add_middleware(PerformanceMonitoringMiddleware, slow_threshold=2.0)

# ──────────────────────────────
# Database Functions - KORRIGIERT
# ──────────────────────────────

def init_db():
    """Initialisiert die Datenbank mit Connection Pool"""
    global CHAT_TABLE_CREATED
    
    with db_manager.pool.get_connection() as conn:
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
                subscription_tier TEXT DEFAULT 'free'
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
        
        cursor.execute("SELECT username FROM users WHERE username = ?", ("admin",))
        if not cursor.fetchone():
            admin_hash = hash_password("admin")
            cursor.execute("""
                INSERT INTO users (username, password, question, answer, is_admin, subscription_tier) 
                VALUES (?, ?, ?, ?, 1, 'premium')
            """, ("admin", admin_hash, "Default Admin Question", "admin"))
            logger.info("[INIT] Admin-User erstellt (admin/admin)")
        
        conn.commit()
    
    CHAT_TABLE_CREATED = True
    logger.info("[INIT] Datenbank mit Connection Pool initialisiert")

def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()

def render_markdown_simple(text: str) -> str:
    # HTML escaping für Sicherheit
    text = html.escape(text)
    
    # Code-Blöcke (```code```)
    text = re.sub(
        r'```(\w+)?\n?(.*?)```', 
        r'<div class="code-block"><div class="code-header">\1</div><pre><code>\2</code></pre></div>', 
        text, 
        flags=re.DOTALL
    )
    
    # Inline-Code (`code`)
    text = re.sub(r'`([^`]+)`', r'<code class="inline-code">\1</code>', text)
    
    # Fett (**text**)
    text = re.sub(r'\*\*([^*]+)\*\*', r'<strong>\1</strong>', text)
    
    # Kursiv (*text*)
    text = re.sub(r'\*([^*]+)\*', r'<em>\1</em>', text)
    
    # Listen (- item)
    lines = text.split('\n')
    in_list = False
    result_lines = []
    
    for line in lines:
        if line.strip().startswith('- '):
            if not in_list:
                result_lines.append('<ul class="chat-list">')
                in_list = True
            result_lines.append(f'<li>{line.strip()[2:]}</li>')
        else:
            if in_list:
                result_lines.append('</ul>')
                in_list = False
            result_lines.append(line)
    
    if in_list:
        result_lines.append('</ul>')
    
    text = '\n'.join(result_lines)
    
    # Links [text](url)
    text = re.sub(
        r'\[([^\]]+)\]\(([^)]+)\)', 
        r'<a href="\2" target="_blank" class="chat-link">\1</a>', 
        text
    )
    
    # Zeilenumbrüche
    text = text.replace('\n', '<br>')
    
    return text

# ──────────────────────────────
# KORRIGIERT: Wrapper-Funktionen für Kompatibilität
# ──────────────────────────────

def get_user(username: str) -> dict:
    """User aus DB holen - uses connection pool"""
    return get_user_pooled(username)

def get_all_users() -> dict:
    """Alle User für Admin-Panel - uses connection pool"""
    return get_all_users_pooled()

def save_user(username: str, password: str, question: str, answer: str) -> bool:
    """Neuen User registrieren - uses connection pool"""
    return save_user_pooled(username, password, question, answer)

# Chat history functions mit Pool
@with_db_connection(db_manager.pool)
def get_user_history(conn, username: str) -> list:
    """Chat-Verlauf mit Connection Pool aus DB laden"""
    if not CHAT_TABLE_CREATED:
        return []
    
    cursor = conn.cursor()
    cursor.execute("""
        SELECT role, content, timestamp FROM chat_history 
        WHERE username = ? ORDER BY timestamp ASC
    """, (username,))
    rows = cursor.fetchall()
    
    return [{"role": row["role"], "content": row["content"], "timestamp": row["timestamp"]} 
            for row in rows]

@with_db_connection(db_manager.pool)
def save_user_history(conn, username: str, role: str, content: str):
    """Chat-Nachricht mit Connection Pool in DB speichern"""
    if not CHAT_TABLE_CREATED:
        return
        
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO chat_history (username, role, content) 
        VALUES (?, ?, ?)
    """, (username, role, content))
    conn.commit()

@with_db_connection(db_manager.pool)
def delete_user_history(conn, username: str):
    """Chat-Verlauf mit Connection Pool löschen"""
    if not CHAT_TABLE_CREATED:
        return
        
    cursor = conn.cursor()
    cursor.execute("DELETE FROM chat_history WHERE username = ?", (username,))
    conn.commit()

def check_login(username: str, password: str) -> bool:
    """Login überprüfen"""
    user = get_user(username)
    if user:
        return user["password"] == hash_password(password)
    return False

def verify_security_answer(username: str, answer: str) -> bool:
    """Sicherheitsantwort prüfen"""
    user = get_user(username)
    if user:
        return user["answer"] == answer
    return False

@with_db_connection(db_manager.pool)
def reset_password(conn, username: str, new_password: str):
    """Passwort zurücksetzen mit Connection Pool"""
    cursor = conn.cursor()
    password_hash = hash_password(new_password)
    cursor.execute("UPDATE users SET password = ? WHERE username = ?", 
                   (password_hash, username))
    conn.commit()

# KORRIGIERT: Persona & Subscription Functions
@with_db_connection(db_manager.pool)
def get_user_persona(conn, username: str) -> str:
    """Holt die gewählte Persona des Users mit Connection Pool"""
    cursor = conn.cursor()
    
    try:
        cursor.execute("SELECT persona FROM users WHERE username = ?", (username,))
        result = cursor.fetchone()
        return result["persona"] if result and result["persona"] else "standard"
    except Exception:
        return "standard"

@with_db_connection(db_manager.pool)
def save_user_persona(conn, username: str, persona: str):
    """Speichert die gewählte Persona des Users mit Connection Pool"""
    cursor = conn.cursor()
    
    cursor.execute("PRAGMA table_info(users)")
    columns = [column["name"] for column in cursor.fetchall()]
    
    if 'persona' not in columns:
        cursor.execute("ALTER TABLE users ADD COLUMN persona TEXT DEFAULT 'standard'")
    
    cursor.execute("UPDATE users SET persona = ? WHERE username = ?", (persona, username))
    conn.commit()

@with_db_connection(db_manager.pool)
def get_user_subscription_tier(conn, username: str) -> str:
    """Holt das Subscription-Tier des Users mit Connection Pool"""
    cursor = conn.cursor()
    
    try:
        cursor.execute("SELECT subscription_tier FROM users WHERE username = ?", (username,))
        result = cursor.fetchone()
        return result["subscription_tier"] if result and result["subscription_tier"] else "free"
    except Exception:
        cursor.execute("ALTER TABLE users ADD COLUMN subscription_tier TEXT DEFAULT 'free'")
        conn.commit()
        return "free"

@with_db_connection(db_manager.pool)
def set_user_subscription_tier(conn, username: str, tier: str):
    """Setzt das Subscription-Tier für einen User mit Connection Pool"""
    if tier not in SUBSCRIPTION_TIERS:
        tier = "free"
    
    cursor = conn.cursor()
    
    try:
        cursor.execute("UPDATE users SET subscription_tier = ? WHERE username = ?", (tier, username))
    except Exception:
        cursor.execute("ALTER TABLE users ADD COLUMN subscription_tier TEXT DEFAULT 'free'")
        cursor.execute("UPDATE users SET subscription_tier = ? WHERE username = ?", (tier, username))
    
    conn.commit()

@with_db_connection(db_manager.pool)
def toggle_user_block(conn, username: str):
    """User blockieren/entblockieren mit Connection Pool"""
    cursor = conn.cursor()
    cursor.execute("SELECT is_blocked FROM users WHERE username = ?", (username,))
    row = cursor.fetchone()
    if not row:
        return
    current = row["is_blocked"]
    new_status = 0 if current else 1
    cursor.execute("UPDATE users SET is_blocked = ? WHERE username = ?", 
                   (new_status, username))
    conn.commit()

@with_db_connection(db_manager.pool)
def delete_user_completely(conn, username: str):
    """User und alle Daten löschen mit Connection Pool"""
    cursor = conn.cursor()
    cursor.execute("DELETE FROM users WHERE username = ?", (username,))
    cursor.execute("DELETE FROM chat_history WHERE username = ?", (username,))
    conn.commit()

def get_available_personas_for_user(username: str) -> dict:
    """Gibt verfügbare Personas basierend auf Subscription zurück"""
    user_tier = get_user_subscription_tier(username)
    tier_config = SUBSCRIPTION_TIERS.get(user_tier, SUBSCRIPTION_TIERS["free"])
    available_persona_keys = tier_config["available_personas"]
    
    return {key: persona for key, persona in PERSONAS.items() 
            if key in available_persona_keys}

# ──────────────────────────────
# Cache Functions - KORRIGIERT
# ──────────────────────────────

@cache_result("user", ttl=600)
def get_user_cached(username: str):
    return get_user_pooled(username)

@cache_result("user_persona", ttl=300)
def get_user_persona_cached(username: str):
    return get_user_persona(username)

@cache_result("user_tier", ttl=600)
def get_user_subscription_tier_cached(username: str):
    return get_user_subscription_tier(username)

@cache_result("available_personas", ttl=600)
def get_available_personas_for_user_cached(username: str):
    return get_available_personas_for_user(username)

@cache_result("all_users", ttl=120)
def get_all_users_cached():
    return get_all_users_pooled()

# Cache invalidation functions - KORRIGIERT
def save_user_persona_cached(username: str, persona: str):
    save_user_persona(username, persona)
    invalidate_user_cache(username)

def set_user_subscription_tier_cached(username: str, tier: str):
    set_user_subscription_tier(username, tier)
    invalidate_user_cache(username)
    app_cache.delete("all_users")  # KORRIGIERT: Entfernte den ":"

def toggle_user_block_cached(username: str):
    toggle_user_block(username)
    invalidate_user_cache(username)
    app_cache.delete("all_users")  # KORRIGIERT: Entfernte den ":"

# ──────────────────────────────
# Rate Limiting & Response Functions
# ──────────────────────────────

def check_enhanced_rate_limit(username: str) -> dict:
    user_tier = get_user_subscription_tier(username)
    tier_config = SUBSCRIPTION_TIERS.get(user_tier, SUBSCRIPTION_TIERS["free"])
    
    max_per_hour = tier_config["max_messages_per_hour"]
    max_per_day = tier_config["max_messages_per_day"]
    
    if max_per_hour == -1:
        return {"allowed": True, "remaining_hour": "∞", "remaining_day": "∞"}
    
    limits = load_rate_limits()
    current_time = _pytime.time()
    
    user_data = limits.get(username, {"messages": [], "daily_messages": [], "last_reset": current_time})
    
    hour_ago = current_time - 3600
    day_ago = current_time - 86400
    
    user_data["messages"] = [msg_time for msg_time in user_data["messages"] if msg_time > hour_ago]
    user_data["daily_messages"] = [msg_time for msg_time in user_data.get("daily_messages", []) if msg_time > day_ago]
    
    hourly_used = len(user_data["messages"])
    daily_used = len(user_data["daily_messages"])
    
    if hourly_used >= max_per_hour or (max_per_day != -1 and daily_used >= max_per_day):
        return {
            "allowed": False, 
            "remaining_hour": max_per_hour - hourly_used,
            "remaining_day": max_per_day - daily_used if max_per_day != -1 else "∞",
            "tier": user_tier
        }
    
    user_data["messages"].append(current_time)
    user_data["daily_messages"].append(current_time)
    limits[username] = user_data
    save_rate_limits(limits)
    
    return {
        "allowed": True,
        "remaining_hour": max_per_hour - hourly_used - 1,
        "remaining_day": max_per_day - daily_used - 1 if max_per_day != -1 else "∞",
        "tier": user_tier
    }

def get_response_with_context(current_message: str, chat_history: list, persona: str = "standard") -> str:
    messages = []
    
    persona_config = PERSONAS.get(persona, PERSONAS["standard"])
    messages.append({
        "role": "system", 
        "content": persona_config["system_prompt"]
    })
    
    for msg in chat_history[-20:]:
        if msg["role"] in ["user", "assistant"]:
            messages.append({
                "role": msg["role"],
                "content": msg["content"]
            })
    
    messages.append({
        "role": "user",
        "content": current_message
    })
    
    return get_response_with_messages(messages)

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
    current_time = _pytime.time()
    
    user_data = limits.get(username, {"messages": [], "last_reset": current_time})
    
    hour_ago = current_time - 3600
    user_data["messages"] = [msg_time for msg_time in user_data["messages"] if msg_time > hour_ago]
    
    if len(user_data["messages"]) >= MESSAGES_PER_HOUR:
        return False
    
    user_data["messages"].append(current_time)
    limits[username] = user_data
    save_rate_limits(limits)
    
    return True

# Session Management
def update_session_activity(request: Request):
    request.session["last_activity"] = _pytime.time()

def check_session_timeout(request: Request) -> bool:
    last_activity = request.session.get("last_activity")
    if not last_activity:
        return True
    
    return (_pytime.time() - last_activity) > SESSION_TIMEOUT_SECONDS

def require_active_session(request: Request):
    if not request.session.get("username"):
        return RedirectResponse("/", status_code=302)
    
    if check_session_timeout(request):
        request.session.clear()
        return RedirectResponse("/?timeout=1", status_code=302)
    
    update_session_activity(request)
    return None

def is_admin(request: Request) -> bool:
    username = request.session.get("username")
    if username:
        user = get_user(username)
        return user and user["is_admin"]
    return False

def admin_redirect_guard(request: Request):
    if not is_admin(request):
        return RedirectResponse("/", status_code=302)
    return None

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
            return templates.TemplateResponse("login.html", {
                "request": request,
                "error": "Du wurdest vom Admin gesperrt."
            })
        
        request.session["username"] = username
        update_session_activity(request)
        
        if user["is_admin"]:
            return RedirectResponse("/admin", status_code=302)
        else:
            return RedirectResponse("/chat", status_code=302)
    
    return templates.TemplateResponse("login.html", {
        "request": request, 
        "error": "Falsche Anmeldedaten"
    })

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
    
    return templates.TemplateResponse("register.html", {
        "request": request, 
        "error": "Benutzer existiert bereits"
    })

@app.get("/reset", response_class=HTMLResponse)
async def reset_page(request: Request):
    return templates.TemplateResponse("reset.html", {
        "request": request, 
        "error": "", 
        "success": ""
    })

@app.post("/reset", response_class=HTMLResponse)
async def reset_post(request: Request, 
                    username: str = Form(...), 
                    answer: str = Form(...), 
                    new_password: str = Form(...)):
    
    if verify_security_answer(username, answer):
        reset_password(username, new_password)
        return RedirectResponse("/", status_code=302)
    
    return templates.TemplateResponse("reset.html", {
        "request": request, 
        "error": "Antwort falsch", 
        "success": ""
    })

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
    if redirect:
        return redirect
    
    username = request.session.get("username")
    history = get_user_history(username)
    
    return templates.TemplateResponse("chat.html", {
        "request": request, 
        "username": username,
        "chat_history": history,
        "session_timeout_minutes": SESSION_TIMEOUT_MINUTES
    })

@app.post("/chat")
async def chat_with_enhanced_limits(req: Request):
    redirect = require_active_session(req)
    if redirect:
        return {"reply": "Session abgelaufen. Bitte neu anmelden.", "redirect": "/"}
    
    username = req.session.get("username")
    
    rate_limit_result = check_enhanced_rate_limit(username)
    if not rate_limit_result["allowed"]:
        tier = rate_limit_result.get("tier", "free")
        tier_name = SUBSCRIPTION_TIERS.get(tier, {}).get("name", tier)
        
        return {
            "reply": f"Rate-Limit erreicht für {tier_name}-Plan! " +
                    f"Verbleibend heute: {rate_limit_result['remaining_day']}, " +
                    f"diese Stunde: {rate_limit_result['remaining_hour']}",
            "rate_limit": True,
            "tier": tier
        }
    
    data = await req.json()
    user_message = data.get("message", "")
    
    if not user_message.strip():
        return {"reply": "Leere Nachricht."}
    
    current_persona = get_user_persona_cached(username)
    available_personas = get_available_personas_for_user_cached(username)
    
    if current_persona not in available_personas:
        current_persona = "standard"
        save_user_persona_cached(username, current_persona)
    
    save_user_history(username, "user", user_message)
    history = get_user_history(username)
    
    try:
        raw_response = get_response_with_context(user_message, history, current_persona)
        rendered_response = render_markdown_simple(raw_response)
        save_user_history(username, "assistant", raw_response)
        
        return {
            "reply": rendered_response,
            "raw_reply": raw_response,
            "remaining_messages": {
                "hour": rate_limit_result["remaining_hour"],
                "day": rate_limit_result["remaining_day"]
            }
        }
        
    except Exception as e:
        logger.error(f"Chat error for {username}: {str(e)}")
        return {"reply": "Ein Fehler ist aufgetreten. Versuche es erneut."}

@app.post("/chat/clear-history")
async def clear_user_history(request: Request):
    redirect = require_active_session(request)
    if redirect:
        return redirect
    
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
    if redirect:
        return redirect
    
    username = request.session.get("username")
    current_persona = get_user_persona_cached(username)
    user_tier = get_user_subscription_tier_cached(username)
    
    available_personas = get_available_personas_for_user_cached(username)
    tier_info = SUBSCRIPTION_TIERS.get(user_tier, SUBSCRIPTION_TIERS["free"])
    
    return templates.TemplateResponse("persona.html", {
        "request": request,
        "username": username,
        "personas": PERSONAS,
        "available_personas": available_personas,
        "current_persona": current_persona,
        "subscription_tier": user_tier,
        "tier_info": tier_info,
        "subscription_tiers": SUBSCRIPTION_TIERS
    })

@app.post("/persona")
async def set_persona(request: Request, persona: str = Form(...)):
    redirect = require_active_session(request)
    if redirect:
        return redirect
    
    username = request.session.get("username")
    available_personas = get_available_personas_for_user_cached(username)
    
    if persona in available_personas:
        save_user_persona_cached(username, persona)
        logger.info(f"[PERSONA] {username} wählte Persona: {persona}")
        return RedirectResponse("/chat", status_code=302)
    else:
        return templates.TemplateResponse("persona.html", {
            "request": request,
            "username": username,
            "personas": PERSONAS,
            "available_personas": available_personas,
            "current_persona": get_user_persona(username),
            "subscription_tier": get_user_subscription_tier(username),
            "tier_info": SUBSCRIPTION_TIERS.get(get_user_subscription_tier(username), SUBSCRIPTION_TIERS["free"]),
            "subscription_tiers": SUBSCRIPTION_TIERS,
            "error": f"Persona '{PERSONAS.get(persona, {}).get('name', persona)}' ist nicht in deinem aktuellen Plan verfügbar."
        })

# ──────────────────────────────
# Routes - Admin
# ──────────────────────────────
@app.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request):
    guard = admin_redirect_guard(request)
    if guard:
        return guard
    
    redirect = require_active_session(request)
    if redirect:
        return redirect
    
    users = get_all_users_cached()
    return templates.TemplateResponse("admin_users.html", {
        "request": request, 
        "users": users,
        "subscription_tiers": SUBSCRIPTION_TIERS
    })

@app.post("/admin/toggle-block")
async def toggle_block_user(request: Request, username: str = Form(...)):
    guard = admin_redirect_guard(request)
    if guard:
        return guard
    
    if username != "admin":
        toggle_user_block_cached(username)
        logger.info(f"[ADMIN] User blockiert/freigeschaltet: {username}")
    
    return RedirectResponse("/admin", status_code=302)

@app.get("/admin/cache-stats")
async def cache_stats(request: Request):
    guard = admin_redirect_guard(request)
    if guard:
        return guard
    
    return {
        "cache_stats": app_cache.stats(),
        "cleanup_available": True
    }

@app.post("/admin/clear-cache")
async def clear_cache(request: Request):
    guard = admin_redirect_guard(request)
    if guard:
        return guard
    
    app_cache.clear()
    logger.info("[CACHE] Admin hat Cache geleert")
    return {"message": "Cache geleert", "success": True}

@app.post("/admin/change-tier")
async def change_user_tier(request: Request, 
                          username: str = Form(...),
                          tier: str = Form(...)):
    guard = admin_redirect_guard(request)
    if guard:
        return guard
    
    if tier in SUBSCRIPTION_TIERS:
        set_user_subscription_tier_cached(username, tier)
        logger.info(f"[ADMIN] Subscription-Tier für {username} geändert zu: {tier}")
    
    return RedirectResponse("/admin", status_code=302)

@app.get("/admin/history/{username}", response_class=HTMLResponse)
async def view_user_history(request: Request, username: str):
    guard = admin_redirect_guard(request)
    if guard:
        return guard
    
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
    if guard:
        return guard
    
    delete_user_history(username)
    logger.info(f"[ADMIN] Chat-Verlauf gelöscht: {username}")
    
    return RedirectResponse("/admin", status_code=302)

@app.post("/admin/delete-user")
async def delete_user(request: Request, username: str = Form(...)):
    guard = admin_redirect_guard(request)
    if guard:
        return guard
    
    if username != "admin":
        delete_user_completely(username)
        logger.info(f"[ADMIN] User gelöscht: {username}")
    
    return RedirectResponse("/admin", status_code=302)

@app.post("/admin/change-user-password")
async def change_user_password(request: Request, 
                              username: str = Form(...), 
                              new_password: str = Form(...)):
    guard = admin_redirect_guard(request)
    if guard:
        return guard
    
    if username != "admin":
        reset_password(username, new_password)
        logger.info(f"[ADMIN] Passwort geändert für: {username}")
    
    return RedirectResponse("/admin", status_code=302)

@app.post("/admin/change-password")
async def change_admin_password(request: Request, 
                               old_password: str = Form(...), 
                               new_password: str = Form(...)):
    guard = admin_redirect_guard(request)
    if guard:
        return guard
    
    admin_user = get_user("admin")
    if admin_user and hmac.compare_digest(admin_user["password"], hash_password(old_password)):
        reset_password("admin", new_password)
        logger.info("[ADMIN] Admin-Passwort geändert")
        return RedirectResponse("/admin", status_code=302)
    else:
        users = get_all_users()
        return templates.TemplateResponse("admin_users.html", {
            "request": request,
            "users": users,
            "subscription_tiers": SUBSCRIPTION_TIERS,
            "error": "Altes Passwort ist falsch"
        })

@app.get("/admin/export-csv")
async def export_csv():
    users = get_all_users()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Benutzername", "Passwort-Hash", "Frage", "Antwort", "Admin", "Blockiert", "Subscription-Tier"])
    
    for name, data in users.items():
        writer.writerow([
            name,
            data.get("password", ""),
            data.get("question", ""),
            data.get("answer", ""),
            "Ja" if data.get("is_admin") else "Nein",
            "Ja" if data.get("blocked") else "Nein",
            data.get("subscription_tier", "free")
        ])
    
    output.seek(0)
    return StreamingResponse(
        io.BytesIO(output.getvalue().encode("utf-8")),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=users.csv"}
    )

@app.get("/admin/performance", response_class=HTMLResponse)
async def admin_performance(request: Request):
    guard = admin_redirect_guard(request)
    if guard:
        return guard
    
    redirect = require_active_session(request)
    if redirect:
        return redirect
    
    stats = PerformanceMonitoringMiddleware.instance.get_stats()
    cache_stats = app_cache.stats()
    
    return templates.TemplateResponse("admin_performance.html", {
        "request": request,
        "stats": stats,
        "cache_stats": cache_stats
    })

@app.get("/api/performance-stats")
async def api_performance_stats(request: Request):
    guard = admin_redirect_guard(request)
    if guard:
        return {"error": "Unauthorized"}
    
    redirect = require_active_session(request)
    if redirect:
        return {"error": "Session expired"}
    
    return PerformanceMonitoringMiddleware.instance.get_stats()

@app.get("/admin/database", response_class=HTMLResponse)
async def admin_database_stats(request: Request):
    guard = admin_redirect_guard(request)
    if guard:
        return guard
    
    redirect = require_active_session(request)
    if redirect:
        return redirect
    
    db_stats = get_database_performance_stats()
    
    return templates.TemplateResponse("admin_database.html", {
        "request": request,
        "db_stats": db_stats
    })

@app.get("/api/database-stats")
async def api_database_stats(request: Request):
    guard = admin_redirect_guard(request)
    if guard:
        return {"error": "Unauthorized"}
    
    redirect = require_active_session(request)
    if redirect:
        return {"error": "Session expired"}
    
    return get_database_performance_stats()

@app.get("/admin/analytics", response_class=HTMLResponse)
async def admin_analytics(request: Request):
    guard = admin_redirect_guard(request)
    if guard:
        return guard
    
    redirect = require_active_session(request)
    if redirect:
        return redirect
    
    users = get_all_users()
    total_users = len(users)
    blocked_users = sum(1 for user in users.values() if user.get("blocked", False))
    admin_users = sum(1 for user in users.values() if user.get("is_admin", False))
    
    tier_stats = {"free": 0, "pro": 0, "premium": 0}
    for user in users.values():
        tier = user.get("subscription_tier", "free")
        if tier in tier_stats:
            tier_stats[tier] += 1
    
    try:
        with db_manager.pool.get_connection() as conn:
            cursor = conn.cursor()
            
            cursor.execute("SELECT COUNT(*) FROM chat_history")
            total_messages = cursor.fetchone()[0]
            
            cursor.execute("""
                SELECT username, COUNT(*) as msg_count 
                FROM chat_history 
                GROUP BY username 
                ORDER BY msg_count DESC 
                LIMIT 10
            """)
            top_users = cursor.fetchall()
            
            cursor.execute("""
                SELECT DATE(timestamp) as date, COUNT(*) as count 
                FROM chat_history 
                WHERE timestamp >= datetime('now', '-7 days')
                GROUP BY DATE(timestamp)
                ORDER BY date DESC
            """)
            daily_stats = cursor.fetchall()
        
    except Exception as e:
        total_messages = 0
        top_users = []
        daily_stats = []
        logger.error(f"Analytics DB error: {e}")
    
    perf_stats = {}
    if hasattr(PerformanceMonitoringMiddleware, 'instance') and PerformanceMonitoringMiddleware.instance:
        perf_stats = PerformanceMonitoringMiddleware.instance.get_stats()
    
    return templates.TemplateResponse("admin_analytics.html", {
        "request": request,
        "total_users": total_users,
        "blocked_users": blocked_users,
        "admin_users": admin_users,
        "tier_stats": tier_stats,
        "total_messages": total_messages,
        "top_users": top_users,
        "daily_stats": daily_stats,
        "perf_stats": perf_stats,
        "subscription_tiers": SUBSCRIPTION_TIERS
    })

# ──────────────────────────────
# API Routes
# ──────────────────────────────
@app.get("/api/session-info")
async def session_info(request: Request):
    if not request.session.get("username"):
        return {"active": False}
    
    last_activity = request.session.get("last_activity", _pytime.time())
    remaining_seconds = max(0, SESSION_TIMEOUT_SECONDS - (_pytime.time() - last_activity))
    
    return {
        "active": True,
        "username": request.session.get("username"),
        "remaining_minutes": int(remaining_seconds / 60),
        "remaining_seconds": int(remaining_seconds)
    }

# ──────────────────────────────
# Background Tasks
# ──────────────────────────────
async def cache_cleanup_background():
    await asyncio.sleep(60)
    
    while True:
        try:
            cleaned = app_cache.cleanup_expired()
            if cleaned > 0:
                logger.info(f"[CACHE] {cleaned} abgelaufene Einträge bereinigt")
            await asyncio.sleep(300)
        except Exception as e:
            logger.error(f"[CACHE ERROR] {e}")
            await asyncio.sleep(300)

# ──────────────────────────────
# Startup & Shutdown
# ──────────────────────────────
@app.on_event("startup")
async def startup():
    init_db()
    asyncio.create_task(cache_cleanup_background())
    logger.info("[STARTUP] KI-Chat mit Connection Pool gestartet")

@app.on_event("shutdown")
async def shutdown():
    db_manager.close()
    logger.info("[SHUTDOWN] Connection Pool geschlossen")