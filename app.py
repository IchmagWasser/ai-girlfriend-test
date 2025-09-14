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
from datetime import datetime
from starlette.middleware.base import BaseHTTPMiddleware
from typing import Dict, List
from collections import defaultdict, deque
import time as _pytime

from ollama_chat import get_response, get_response_with_messages

# ──────────────────────────────
# Setup
# ──────────────────────────────
load_dotenv()
SECRET_KEY = os.getenv("SECRET_KEY", "supersecret")

app = FastAPI()

# Dann erst die Session Middleware:
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

# ──────────────────────────────
# Subscription Tiers Definition
# ──────────────────────────────
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
        "max_messages_per_hour": -1,  # Unlimited
        "max_messages_per_day": -1,   # Unlimited
        "available_personas": ["standard", "freundlich", "lustig", "professionell", "lehrerin", "kreativ", "analyst", "therapeut"],
        "features": ["Alle Personas", "Unbegrenzte Nachrichten", "Höchste Priorität", "Erweiterte Features"]
    }
}

# Enhanced Persona-Definitionen mit Tier-Zuordnung
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

# ──────────────────────────────
# Performance Monitoring Middleware
# ──────────────────────────────

class PerformanceMonitoringMiddleware(BaseHTTPMiddleware):
    """
    Middleware für Performance-Monitoring
    - Misst Request-Zeiten
    - Sammelt Statistiken
    - Speichert langsame Requests
    """

    instance = None  # Statische Referenz auf die Instanz
    
    def __init__(self, app, slow_threshold: float = 2.0):
        super().__init__(app)
        self.slow_threshold = slow_threshold
        self.stats = {
            'total_requests': 0,
            'total_time': 0.0,
            'slow_requests': deque(maxlen=100),  # Letzte 100 langsame Requests
            'endpoint_stats': defaultdict(lambda: {'count': 0, 'total_time': 0.0}),
            'last_requests': deque(maxlen=50)  # Letzte 50 Requests
        }
        PerformanceMonitoringMiddleware.instance = self  # Instanz merken

    async def dispatch(self, request: Request, call_next):
        start_time = _pytime.time()

        # Request verarbeiten
        response = await call_next(request)

        # Performance messen
        process_time = _pytime.time() - start_time

        # Statistiken aktualisieren
        self._update_stats(request, process_time)

        # Performance-Header hinzufügen
        response.headers["X-Process-Time"] = str(round(process_time, 4))

        return response

    def _update_stats(self, request: Request, process_time: float):
        """Interne Statistiken aktualisieren"""
        path = request.url.path
        method = request.method
        endpoint = f"{method} {path}"

        # Global stats
        self.stats['total_requests'] += 1
        self.stats['total_time'] += process_time

        # Endpoint stats
        self.stats['endpoint_stats'][endpoint]['count'] += 1
        self.stats['endpoint_stats'][endpoint]['total_time'] += process_time

        # Langsame Requests tracken
        if process_time > self.slow_threshold:
            self.stats['slow_requests'].append({
                'timestamp': _pytime.time(),
                'endpoint': endpoint,
                'duration': round(process_time, 4),
                'user_agent': request.headers.get('user-agent', 'Unknown')[:100]
            })

        # Letzte Requests tracken
        self.stats['last_requests'].append({
            'timestamp': _pytime.time(),
            'endpoint': endpoint,
            'duration': round(process_time, 4),
            'status': 'slow' if process_time > self.slow_threshold else 'normal'
        })

    def get_stats(self) -> Dict:
        """Performance-Statistiken zurückgeben"""
        avg_time = (self.stats['total_time'] / self.stats['total_requests']
                   if self.stats['total_requests'] > 0 else 0)

        # Endpoint-Statistiken aufbereiten
        endpoint_stats = []
        for endpoint, data in self.stats['endpoint_stats'].items():
            avg_endpoint_time = data['total_time'] / data['count']
            endpoint_stats.append({
                'endpoint': endpoint,
                'count': data['count'],
                'avg_time': round(avg_endpoint_time, 4),
                'total_time': round(data['total_time'], 4)
            })

        # Nach durchschnittlicher Zeit sortieren
        endpoint_stats.sort(key=lambda x: x['avg_time'], reverse=True)

        return {
            'total_requests': self.stats['total_requests'],
            'average_response_time': round(avg_time, 4),
            'total_time': round(self.stats['total_time'], 4),
            'slow_requests_count': len(self.stats['slow_requests']),
            'slow_threshold': self.slow_threshold,
            'endpoint_stats': endpoint_stats[:10],  # Top 10 langsamste
            'recent_slow_requests': list(self.stats['slow_requests'])[-10:],  # Letzte 10 langsame
            'recent_requests': list(self.stats['last_requests'])[-20:]  # Letzte 20 allgemein
        }

# Middleware einbinden
app.add_middleware(PerformanceMonitoringMiddleware, slow_threshold=2.0)

# ──────────────────────────────
# Database Helper Functions
# ──────────────────────────────
def init_db():
    """Initialisiert die Datenbank mit allen nötigen Tabellen"""
    global CHAT_TABLE_CREATED
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Users-Tabelle (konsistent mit deinem reset.py)
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
    
    # Chat-Verlauf Tabelle
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
    
    # Admin-User erstellen falls nicht existiert
    cursor.execute("SELECT username FROM users WHERE username = ?", ("admin",))
    if not cursor.fetchone():
        admin_hash = hash_password("admin")
        cursor.execute("""
            INSERT INTO users (username, password, question, answer, is_admin, subscription_tier) 
            VALUES (?, ?, ?, ?, 1, 'premium')
        """, ("admin", admin_hash, "Default Admin Question", "admin"))
        logger.info("[INIT] Admin-User erstellt (admin/admin)")
    
    conn.commit()
    conn.close()
    CHAT_TABLE_CREATED = True
    logger.info("[INIT] Datenbank initialisiert")

def hash_password(password: str) -> str:
    """Passwort hashen"""
    return hashlib.sha256(password.encode()).hexdigest()

def render_markdown_simple(text: str) -> str:
    """
    Einfaches Markdown-Rendering für Chat-Nachrichten
    Unterstützt: Code-Blöcke, Inline-Code, Listen, Links, Fett/Kursiv
    """
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

def get_user(username: str) -> dict:
    """User aus DB holen"""
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
            "subscription_tier": row[7] if len(row) > 7 else "free"
        }
    return None

def get_all_users() -> dict:
    """Alle User für Admin-Panel"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users")
    rows = cursor.fetchall()
    conn.close()
    
    users = {}
    for row in rows:
        users[row[0]] = {
            "password": row[1],
            "question": row[2],
            "answer": row[3],
            "is_admin": bool(row[4]),
            "blocked": bool(row[5]),  # für Template-Kompatibilität
            "subscription_tier": row[7] if len(row) > 7 else "free"
        }
    return users

def save_user(username: str, password: str, question: str, answer: str) -> bool:
    """Neuen User registrieren"""
    if get_user(username):
        return False  # User existiert bereits
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    try:
        password_hash = hash_password(password)
        cursor.execute("""
            INSERT INTO users (username, password, question, answer, subscription_tier) 
            VALUES (?, ?, ?, ?, 'free')
        """, (username, password_hash, question, answer))
        conn.commit()
        logger.info(f"[REGISTER] Neuer User: {username}")
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        conn.close()

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

def reset_password(username: str, new_password: str):
    """Passwort zurücksetzen"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    password_hash = hash_password(new_password)
    cursor.execute("UPDATE users SET password = ? WHERE username = ?", 
                   (password_hash, username))
    conn.commit()
    conn.close()

def get_user_history(username: str) -> list:
    """Chat-Verlauf aus DB laden"""
    if not CHAT_TABLE_CREATED:
        return []
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT role, content, timestamp FROM chat_history 
        WHERE username = ? ORDER BY timestamp ASC
    """, (username,))
    rows = cursor.fetchall()
    conn.close()
    
    return [{"role": row[0], "content": row[1], "timestamp": row[2]} for row in rows]

def save_user_history(username: str, role: str, content: str):
    """Chat-Nachricht in DB speichern"""
    if not CHAT_TABLE_CREATED:
        return
        
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO chat_history (username, role, content) 
        VALUES (?, ?, ?)
    """, (username, role, content))
    conn.commit()
    conn.close()

def delete_user_history(username: str):
    """Chat-Verlauf löschen"""
    if not CHAT_TABLE_CREATED:
        return
        
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM chat_history WHERE username = ?", (username,))
    conn.commit()
    conn.close()

def toggle_user_block(username: str):
    """User blockieren/entblockieren"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT is_blocked FROM users WHERE username = ?", (username,))
    current = cursor.fetchone()[0]
    new_status = 0 if current else 1
    cursor.execute("UPDATE users SET is_blocked = ? WHERE username = ?", 
                   (new_status, username))
    conn.commit()
    conn.close()

def delete_user_completely(username: str):
    """User und alle Daten löschen"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM users WHERE username = ?", (username,))
    cursor.execute("DELETE FROM chat_history WHERE username = ?", (username,))
    conn.commit()
    conn.close()

def get_user_persona(username: str) -> str:
    """Holt die gewählte Persona des Users"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    try:
        cursor.execute("SELECT persona FROM users WHERE username = ?", (username,))
        result = cursor.fetchone()
        return result[0] if result and result[0] else "standard"
    except sqlite3.OperationalError:
        # Spalte existiert noch nicht
        return "standard"
    finally:
        conn.close()

def save_user_persona(username: str, persona: str):
    """Speichert die gewählte Persona des Users"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Prüfen ob users Tabelle persona Spalte hat, falls nicht hinzufügen
    cursor.execute("PRAGMA table_info(users)")
    columns = [column[1] for column in cursor.fetchall()]
    
    if 'persona' not in columns:
        cursor.execute("ALTER TABLE users ADD COLUMN persona TEXT DEFAULT 'standard'")
    
    cursor.execute("UPDATE users SET persona = ? WHERE username = ?", (persona, username))
    conn.commit()
    conn.close()

# ──────────────────────────────
# Enhanced Subscription Functions
# ──────────────────────────────
def get_user_subscription_tier(username: str) -> str:
    """Holt das Subscription-Tier des Users"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    try:
        cursor.execute("SELECT subscription_tier FROM users WHERE username = ?", (username,))
        result = cursor.fetchone()
        return result[0] if result and result[0] else "free"
    except sqlite3.OperationalError:
        # Spalte existiert noch nicht - hinzufügen
        cursor.execute("ALTER TABLE users ADD COLUMN subscription_tier TEXT DEFAULT 'free'")
        conn.commit()
        return "free"
    finally:
        conn.close()

def set_user_subscription_tier(username: str, tier: str):
    """Setzt das Subscription-Tier für einen User"""
    if tier not in SUBSCRIPTION_TIERS:
        tier = "free"
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Sicherstellen, dass Spalte existiert
    try:
        cursor.execute("UPDATE users SET subscription_tier = ? WHERE username = ?", (tier, username))
    except sqlite3.OperationalError:
        cursor.execute("ALTER TABLE users ADD COLUMN subscription_tier TEXT DEFAULT 'free'")
        cursor.execute("UPDATE users SET subscription_tier = ? WHERE username = ?", (tier, username))
    
    conn.commit()
    conn.close()

def get_available_personas_for_user(username: str) -> dict:
    """Gibt verfügbare Personas basierend auf Subscription zurück"""
    user_tier = get_user_subscription_tier(username)
    tier_config = SUBSCRIPTION_TIERS.get(user_tier, SUBSCRIPTION_TIERS["free"])
    available_persona_keys = tier_config["available_personas"]
    
    return {key: persona for key, persona in PERSONAS.items() 
            if key in available_persona_keys}

def check_enhanced_rate_limit(username: str) -> dict:
    """Erweiterte Rate-Limit-Prüfung basierend auf Subscription"""
    user_tier = get_user_subscription_tier(username)
    tier_config = SUBSCRIPTION_TIERS.get(user_tier, SUBSCRIPTION_TIERS["free"])
    
    max_per_hour = tier_config["max_messages_per_hour"]
    max_per_day = tier_config["max_messages_per_day"]
    
    # Unlimited für Premium
    if max_per_hour == -1:
        return {"allowed": True, "remaining_hour": "∞", "remaining_day": "∞"}
    
    limits = load_rate_limits()
    current_time = _pytime.time()
    
    user_data = limits.get(username, {"messages": [], "daily_messages": [], "last_reset": current_time})
    
    # Alte Nachrichten entfernen
    hour_ago = current_time - 3600
    day_ago = current_time - 86400
    
    user_data["messages"] = [msg_time for msg_time in user_data["messages"] if msg_time > hour_ago]
    user_data["daily_messages"] = [msg_time for msg_time in user_data.get("daily_messages", []) if msg_time > day_ago]
    
    # Prüfen
    hourly_used = len(user_data["messages"])
    daily_used = len(user_data["daily_messages"])
    
    if hourly_used >= max_per_hour or (max_per_day != -1 and daily_used >= max_per_day):
        return {
            "allowed": False, 
            "remaining_hour": max_per_hour - hourly_used,
            "remaining_day": max_per_day - daily_used if max_per_day != -1 else "∞",
            "tier": user_tier
        }
    
    # Neue Nachricht hinzufügen
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
    """
    Holt KI-Antwort mit Chat-Kontext und Persona
    """
    # Chat-Historie in das richtige Format für Ollama konvertieren
    messages = []
    
    # System-Prompt basierend auf gewählter Persona
    persona_config = PERSONAS.get(persona, PERSONAS["standard"])
    messages.append({
        "role": "system", 
        "content": persona_config["system_prompt"]
    })
    
    # Chat-Historie hinzufügen (letzte 20 Nachrichten für besseren Kontext)
    for msg in chat_history[-20:]:
        if msg["role"] in ["user", "assistant"]:
            messages.append({
                "role": msg["role"],
                "content": msg["content"]
            })
    
    # Aktuelle Nachricht hinzufügen
    messages.append({
        "role": "user",
        "content": current_message
    })
    
    # An get_response weitergeben
    return get_response_with_messages(messages)

# Rate-Limiting Funktionen
def load_rate_limits():
    """Lädt Rate-Limit-Daten"""
    if os.path.exists(RATE_LIMIT_FILE):
        try:
            with open(RATE_LIMIT_FILE, 'r') as f:
                return json.load(f)
        except:
            return {}
    return {}

def save_rate_limits(limits):
    """Speichert Rate-Limit-Daten"""
    with open(RATE_LIMIT_FILE, 'w') as f:
        json.dump(limits, f)

def check_rate_limit(username: str) -> bool:
    """Prüft ob User Rate-Limit erreicht hat"""
    limits = load_rate_limits()
    current_time = _pytime.time()
    
    user_data = limits.get(username, {"messages": [], "last_reset": current_time})
    
    # Alte Nachrichten entfernen (älter als 1 Stunde)
    hour_ago = current_time - 3600
    user_data["messages"] = [msg_time for msg_time in user_data["messages"] if msg_time > hour_ago]
    
    # Prüfen ob Limit erreicht
    if len(user_data["messages"]) >= MESSAGES_PER_HOUR:
        return False
    
    # Neue Nachricht hinzufügen
    user_data["messages"].append(current_time)
    limits[username] = user_data
    save_rate_limits(limits)
    
    return True

# Session-Management
def update_session_activity(request: Request):
    """Aktualisiert die letzte Aktivität in der Session"""
    request.session["last_activity"] = _pytime.time()

def check_session_timeout(request: Request) -> bool:
    """Prüft ob Session abgelaufen ist"""
    last_activity = request.session.get("last_activity")
    if not last_activity:
        return True
    
    return (_pytime.time() - last_activity) > SESSION_TIMEOUT_SECONDS

def require_active_session(request: Request):
    """Middleware-ähnliche Funktion für Session-Check"""
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
    # Session-Check
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
    """Chat mit erweiterten Rate-Limits basierend auf Subscription"""
    redirect = require_active_session(req)
    if redirect:
        return {"reply": "Session abgelaufen. Bitte neu anmelden.", "redirect": "/"}
    
    username = req.session.get("username")
    
    # Erweiterte Rate-Limit-Prüfung
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
    
    # Persona-Verfügbarkeit prüfen
    current_persona = get_user_persona(username)
    available_personas = get_available_personas_for_user(username)
    
    if current_persona not in available_personas:
        # Fallback zu Standard-Persona
        current_persona = "standard"
        save_user_persona(username, current_persona)
    
    # User-Nachricht speichern
    save_user_history(username, "user", user_message)
    
    # Chat-Historie für Kontext laden
    history = get_user_history(username)
    
    try:
        # Raw-Antwort von KI holen
        raw_response = get_response_with_context(user_message, history, current_persona)
        
        # Markdown rendern
        rendered_response = render_markdown_simple(raw_response)
        
        # Raw-Version in DB speichern (für Verlauf)
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
    """User kann seinen eigenen Chat-Verlauf löschen"""
    redirect = require_active_session(request)
    if redirect:
        return redirect
    
    username = request.session.get("username")
    
    # Chat-Verlauf für den User löschen
    delete_user_history(username)
    logger.info(f"[USER] {username} hat seinen Chat-Verlauf gelöscht")
    
    return RedirectResponse("/chat", status_code=302)

# ──────────────────────────────
# Routes - Persona
# ──────────────────────────────
@app.get("/persona", response_class=HTMLResponse)
async def persona_settings(request: Request):
    """Persona-Einstellungen Seite mit Subscription-Support"""
    redirect = require_active_session(request)
    if redirect:
        return redirect
    
    username = request.session.get("username")
    current_persona = get_user_persona(username)
    user_tier = get_user_subscription_tier(username)
    
    # Verfügbare Personas für User-Tier
    available_personas = get_available_personas_for_user(username)
    
    # Tier-Informationen
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
    """Persona auswählen mit Subscription-Prüfung"""
    redirect = require_active_session(request)
    if redirect:
        return redirect
    
    username = request.session.get("username")
    available_personas = get_available_personas_for_user(username)
    
    if persona in available_personas:
        save_user_persona(username, persona)
        logger.info(f"[PERSONA] {username} wählte Persona: {persona}")
        return RedirectResponse("/chat", status_code=302)
    else:
        # Persona nicht verfügbar für User-Tier
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
    
    # Session-Check auch für Admin
    redirect = require_active_session(request)
    if redirect:
        return redirect
    
    users = get_all_users()
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
    
    if username != "admin":  # Admin kann sich nicht selbst blockieren
        toggle_user_block(username)
        logger.info(f"[ADMIN] User blockiert/freigeschaltet: {username}")
    
    return RedirectResponse("/admin", status_code=302)

@app.post("/admin/change-tier")
async def change_user_tier(request: Request, 
                          username: str = Form(...),
                          tier: str = Form(...)):
    """Admin kann User-Subscription-Tier ändern"""
    guard = admin_redirect_guard(request)
    if guard:
        return guard
    
    if tier in SUBSCRIPTION_TIERS:
        set_user_subscription_tier(username, tier)
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
    
    if username != "admin":  # Admin kann sich nicht selbst löschen
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
    
    if username != "admin":  # Admin-Passwort separat ändern
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
    """Admin-Seite für Performance-Monitoring"""
    guard = admin_redirect_guard(request)
    if guard:
        return guard
    
    # Session-Check auch für Admin
    redirect = require_active_session(request)
    if redirect:
        return redirect
    
    # Performance-Daten vom globalen Monitor holen
    stats = PerformanceMonitoringMiddleware.instance.get_stats()

    
    return templates.TemplateResponse("admin_performance.html", {
        "request": request,
        "stats": stats
    })

@app.get("/api/performance-stats")
async def api_performance_stats(request: Request):
    """API für Performance-Statistiken (für AJAX-Updates)"""
    guard = admin_redirect_guard(request)
    if guard:
        return {"error": "Unauthorized"}
    
    redirect = require_active_session(request)
    if redirect:
        return {"error": "Session expired"}
    

@app.get("/admin/analytics", response_class=HTMLResponse)
async def admin_analytics(request: Request):
    """Admin-Analytics Seite"""
    guard = admin_redirect_guard(request)
    if guard:
        return guard
    
    redirect = require_active_session(request)
    if redirect:
        return redirect
    
    # Benutzer-Statistiken sammeln
    users = get_all_users()
    total_users = len(users)
    blocked_users = sum(1 for user in users.values() if user.get("blocked", False))
    admin_users = sum(1 for user in users.values() if user.get("is_admin", False))
    
    # Subscription-Tier-Verteilung
    tier_stats = {"free": 0, "pro": 0, "premium": 0}
    for user in users.values():
        tier = user.get("subscription_tier", "free")
        if tier in tier_stats:
            tier_stats[tier] += 1
    
    # Chat-Statistiken (falls verfügbar)
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # Gesamte Chat-Nachrichten
        cursor.execute("SELECT COUNT(*) FROM chat_history")
        total_messages = cursor.fetchone()[0]
        
        # Nachrichten pro Benutzer
        cursor.execute("""
            SELECT username, COUNT(*) as msg_count 
            FROM chat_history 
            GROUP BY username 
            ORDER BY msg_count DESC 
            LIMIT 10
        """)
        top_users = cursor.fetchall()
        
        # Nachrichten der letzten 7 Tage
        cursor.execute("""
            SELECT DATE(timestamp) as date, COUNT(*) as count 
            FROM chat_history 
            WHERE timestamp >= datetime('now', '-7 days')
            GROUP BY DATE(timestamp)
            ORDER BY date DESC
        """)
        daily_stats = cursor.fetchall()
        
        conn.close()
        
    except Exception as e:
        total_messages = 0
        top_users = []
        daily_stats = []
        logger.error(f"Analytics DB error: {e}")
    
    # Performance-Stats (falls Middleware aktiv)
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
    

    return PerformanceMonitoringMiddleware.instance.get_stats()

# ──────────────────────────────
# API Routes
# ──────────────────────────────
@app.get("/api/session-info")
async def session_info(request: Request):
    """API für Session-Status"""
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
# Startup
# ──────────────────────────────
@app.on_event("startup")
def startup():
    init_db()
    logger.info("[STARTUP] KI-Chat mit Subscription-System gestartet")