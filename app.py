from fastapi import FastAPI, Request, Form, HTTPException, Header, File, UploadFile, BackgroundTasks
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse, Response, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from dotenv import load_dotenv
from typing import Optional, List, Dict, Any
from enum import Enum
from dataclasses import dataclass
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
import uuid
import secrets
import zipfile
import asyncio
import threading
import queue
from datetime import datetime, timedelta
from time import time as time_now
from collections import defaultdict, OrderedDict

# Optional imports with fallbacks
try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False
    psutil = None

try:
    import magic
    MAGIC_AVAILABLE = True
except ImportError:
    MAGIC_AVAILABLE = False
    magic = None

try:
    from PIL import Image
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False
    Image = None

try:
    import PyPDF2
    PDF_AVAILABLE = True
except ImportError:
    PDF_AVAILABLE = False
    PyPDF2 = None

try:
    import docx
    DOCX_AVAILABLE = True
except ImportError:
    DOCX_AVAILABLE = False
    docx = None

from concurrent.futures import ThreadPoolExecutor
import aiofiles
import httpx

from ollama_chat import get_response, get_response_with_messages

# ──────────────────────────────
# Enhanced Configuration & Setup
# ──────────────────────────────
load_dotenv()
SECRET_KEY = os.getenv("SECRET_KEY", "supersecret")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
UPLOAD_DIR = os.getenv("UPLOAD_DIR", "uploads")
MAX_UPLOAD_SIZE = int(os.getenv("MAX_UPLOAD_SIZE", "10485760"))  # 10MB
REDIS_URL = os.getenv("REDIS_URL", None)

# Ensure directories exist
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs("logs", exist_ok=True)
os.makedirs("static", exist_ok=True)
os.makedirs("templates", exist_ok=True)

app = FastAPI(title="KI-Chat Advanced", version="2.0.0")
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY)
app.mount("/static", StaticFiles(directory="static"), name="static")
app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")

templates = Jinja2Templates(directory="templates")

# ──────────────────────────────
# Enhanced Logging System
# ──────────────────────────────
import structlog
import logging.handlers

def setup_structured_logging():
    """Setup structured logging with JSON format"""
    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="ISO"),
            structlog.processors.add_log_level,
            structlog.processors.JSONRenderer()
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        logger_factory=structlog.stdlib.LoggerFactory(),
        context_class=dict,
        cache_logger_on_first_use=True,
    )
    
    # File handler for structured logs
    handler = logging.handlers.RotatingFileHandler(
        "logs/app.json",
        maxBytes=10485760,  # 10MB
        backupCount=5
    )
    handler.setLevel(logging.INFO)
    
    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    
    # Root logger setup
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.addHandler(handler)
    root_logger.addHandler(console_handler)

setup_structured_logging()
logger = structlog.get_logger("app")

# File paths
DB_PATH = "users.db"
RATE_LIMIT_FILE = "rate_limits.json"
ANALYTICS_FILE = "analytics.json"
SESSIONS_DB = "sessions.db"
PERFORMANCE_LOG = "logs/performance.json"

# Settings
CHAT_TABLE_CREATED = False
MESSAGES_PER_HOUR = 50
SESSION_TIMEOUT_MINUTES = 30
SESSION_TIMEOUT_SECONDS = SESSION_TIMEOUT_MINUTES * 60
# ──────────────────────────────
# Performance Monitoring
# ──────────────────────────────
@dataclass
class PerformanceMetrics:
    endpoint: str
    method: str
    duration: float
    status_code: int
    timestamp: str
    user: Optional[str] = None
    db_queries: int = 0
    cache_hits: int = 0
    cache_misses: int = 0

class PerformanceMonitor:
    def __init__(self):
        self.metrics = []
        self.lock = threading.Lock()
    
    def record_metric(self, metric: PerformanceMetrics):
        with self.lock:
            self.metrics.append(metric)
            # Keep only last 1000 metrics
            if len(self.metrics) > 1000:
                self.metrics = self.metrics[-1000:]
    
    def get_stats(self) -> Dict[str, Any]:
        with self.lock:
            if not self.metrics:
                return {}
            
            durations = [m.duration for m in self.metrics]
            return {
                "total_requests": len(self.metrics),
                "avg_response_time": sum(durations) / len(durations) if durations else 0.0,
                "max_response_time": max(durations) if durations else 0.0,
                "min_response_time": min(durations) if durations else 0.0,
                "endpoints": self._get_endpoint_stats(),
                "last_updated": datetime.now().isoformat()
            }
    
    def _get_endpoint_stats(self):
        endpoint_stats = defaultdict(list)
        for metric in self.metrics:
            endpoint_stats[metric.endpoint].append(metric.duration)
        
        return {
            endpoint: {
                "count": len(durations),
                "avg_time": sum(durations) / len(durations) if durations else 0.0,
                "max_time": max(durations) if durations else 0.0
            } for endpoint, durations in endpoint_stats.items()
        }

performance_monitor = PerformanceMonitor()

# ──────────────────────────────
# Enhanced Caching System
# ──────────────────────────────
class MemoryCache:
    def __init__(self, max_size: int = 1000, ttl_seconds: int = 3600):
        self.cache = OrderedDict()
        self.timestamps = {}
        self.max_size = max_size
        self.ttl = ttl_seconds
        self.lock = threading.Lock()
        self.hits = 0
        self.misses = 0
    
    def get(self, key: str) -> Optional[Any]:
        with self.lock:
            if key not in self.cache:
                self.misses += 1
                return None
            
            # Check TTL
            if time_now() - self.timestamps.get(key, 0) > self.ttl:
                self.cache.pop(key, None)
                self.timestamps.pop(key, None)
                self.misses += 1
                return None
            
            # Move to end (LRU)
            self.cache.move_to_end(key)
            self.hits += 1
            return self.cache[key]
    
    def set(self, key: str, value: Any):
        with self.lock:
            if key in self.cache:
                del self.cache[key]
            self.cache[key] = value
            self.timestamps[key] = time_now()
            # Remove oldest if over limit
            while len(self.cache) > self.max_size:
                oldest_key = next(iter(self.cache))
                del self.cache[oldest_key]
                del self.timestamps[oldest_key]
    
    def delete(self, key: str):
        with self.lock:
            if key in self.cache:
                del self.cache[key]
                del self.timestamps[key]
    
    def clear(self):
        with self.lock:
            self.cache.clear()
            self.timestamps.clear()
    
    def stats(self):
        total = self.hits + self.misses
        return {
            "size": len(self.cache),
            "max_size": self.max_size,
            "hits": self.hits,
            "misses": self.misses,
            "hit_rate": self.hits / total if total else 0
        }

cache = MemoryCache()

# ──────────────────────────────
# Connection Pool Manager
# ──────────────────────────────
class DatabasePool:
    def __init__(self, database_path: str, pool_size: int = 5):
        self.database_path = database_path
        self.pool_size = pool_size
        self.pool = queue.Queue(maxsize=pool_size)
        self.lock = threading.Lock()
        self.active_connections = 0
        
        # Initialize pool
        for _ in range(pool_size):
            conn = sqlite3.connect(database_path, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            self.pool.put(conn)
    
    def get_connection(self) -> sqlite3.Connection:
        try:
            conn = self.pool.get_nowait()
            return conn
        except queue.Empty:
            # Pool exhausted, create temporary connection
            with self.lock:
                self.active_connections += 1
            conn = sqlite3.connect(self.database_path, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            return conn
    
    def return_connection(self, conn: sqlite3.Connection):
        try:
            # Reset connection state
            try:
                conn.rollback()
            except Exception:
                pass
            self.pool.put_nowait(conn)
        except queue.Full:
            # Pool full, close connection
            conn.close()
            with self.lock:
                self.active_connections -= 1
    
    def close_all(self):
        while not self.pool.empty():
            conn = self.pool.get()
            conn.close()

db_pool = DatabasePool(DB_PATH)

# Context manager for database connections
class DatabaseConnection:
    def __init__(self, pool: DatabasePool):
        self.pool = pool
        self.conn = None
    
    def __enter__(self) -> sqlite3.Connection:
        self.conn = self.pool.get_connection()
        return self.conn
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.conn:
            if exc_type:
                self.conn.rollback()
            else:
                self.conn.commit()
            self.pool.return_connection(self.conn)

# ──────────────────────────────
# Background Task System
# ──────────────────────────────
class BackgroundTaskManager:
    def __init__(self):
        self.task_queue = queue.Queue()
        self.executor = ThreadPoolExecutor(max_workers=3)
        self.running = True
        self.worker_thread = threading.Thread(target=self._worker, daemon=True)
        self.worker_thread.start()
    
    def add_task(self, func, *args, **kwargs):
        """Add a task to the background queue"""
        self.task_queue.put((func, args, kwargs))
    
    def _worker(self):
        """Background worker thread"""
        while self.running:
            try:
                func, args, kwargs = self.task_queue.get(timeout=1)
                self.executor.submit(func, *args, **kwargs)
                self.task_queue.task_done()
            except queue.Empty:
                continue
            except Exception as e:
                logger.error("Background task failed", error=str(e))
    
    def shutdown(self):
        self.running = False
        self.executor.shutdown(wait=True)

background_tasks = BackgroundTaskManager()

# ──────────────────────────────
# File Processing System
# ──────────────────────────────
class FileProcessor:
    ALLOWED_TYPES = {
        'image': ['image/jpeg', 'image/png', 'image/gif', 'image/webp'],
        'document': ['application/pdf', 'text/plain', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'],
        'text': ['text/plain', 'text/markdown', 'text/csv']
    }
    
    @staticmethod
    async def process_file(file: UploadFile) -> Dict[str, Any]:
        """Process uploaded file and extract content"""
        content = await file.read()
        
        # Detect file type (fallback if magic is not available)
        if MAGIC_AVAILABLE:
            try:
                mime_type = magic.from_buffer(content, mime=True)
            except Exception:
                mime_type = 'application/octet-stream'
        else:
            # Simple fallback based on file extension
            filename = (file.filename or "").lower()
            if filename.endswith(('.jpg', '.jpeg', '.png', '.gif', '.webp')):
                mime_type = 'image/jpeg'
            elif filename.endswith('.pdf'):
                mime_type = 'application/pdf'
            elif filename.endswith('.docx'):
                mime_type = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
            elif filename.endswith('.txt'):
                mime_type = 'text/plain'
            else:
                mime_type = 'application/octet-stream'
        
        result = {
            'filename': file.filename,
            'mime_type': mime_type,
            'size': len(content),
            'content': '',
            'metadata': {}
        }
        
        try:
            if mime_type in FileProcessor.ALLOWED_TYPES['image']:
                result['content'] = await FileProcessor._process_image(content)
                result['type'] = 'image'
            
            elif mime_type in FileProcessor.ALLOWED_TYPES['document']:
                result['content'] = await FileProcessor._process_document(content, mime_type)
                result['type'] = 'document'
            
            elif mime_type in FileProcessor.ALLOWED_TYPES['text']:
                result['content'] = content.decode('utf-8', errors='replace')
                result['type'] = 'text'
            
            else:
                raise ValueError(f"Unsupported file type: {mime_type}")
        
        except Exception as e:
            result['error'] = str(e)
        
        return result
    
    @staticmethod
    async def _process_image(content: bytes) -> str:
        """Process image file"""
        if not PIL_AVAILABLE:
            return "Bildverarbeitung nicht verfügbar (Pillow ist nicht installiert)."
        try:
            with Image.open(io.BytesIO(content)) as img:
                return f"Bild analysiert: {img.format} Format, Größe: {img.size[0]}x{img.size[1]} Pixel. Bereit für KI-Analyse."
        except Exception as e:
            return f"Fehler beim Verarbeiten des Bildes: {str(e)}"
    
    @staticmethod
    async def _process_document(content: bytes, mime_type: str) -> str:
        """Extract text from document files"""
        try:
            if mime_type == 'application/pdf':
                if not PDF_AVAILABLE:
                    return "PDF-Verarbeitung nicht verfügbar (PyPDF2 nicht installiert)."
                pdf_reader = PyPDF2.PdfReader(io.BytesIO(content))
                text = ""
                for page in pdf_reader.pages:
                    try:
                        text += (page.extract_text() or "") + "\n"
                    except Exception:
                        continue
                return text
            
            elif mime_type == 'application/vnd.openxmlformats-officedocument.wordprocessingml.document':
                if not DOCX_AVAILABLE:
                    return "DOCX-Verarbeitung nicht verfügbar (python-docx nicht installiert)."
                doc = docx.Document(io.BytesIO(content))
                text = ""
                for paragraph in doc.paragraphs:
                    text += paragraph.text + "\n"
                return text
            
            elif mime_type == 'text/plain':
                return content.decode('utf-8', errors='replace')
            
            else:
                return "Dokumententyp wird nicht unterstützt."
        except Exception as e:
            return f"Fehler beim Verarbeiten des Dokuments: {str(e)}"
# ──────────────────────────────
# Multiple AI Models Support
# ──────────────────────────────
class AIModelManager:
    def __init__(self):
        self.models = {
            'ollama': {
                'name': 'Llama 3.2 (Local)',
                'description': 'Schnelles lokales Modell via Ollama',
                'available': True,
                'cost_per_1k': 0,
                'max_tokens': 4096
            },
            'openai-gpt-3.5': {
                'name': 'GPT-3.5 Turbo',
                'description': 'OpenAI GPT-3.5 Turbo Modell',
                'available': bool(OPENAI_API_KEY),
                'cost_per_1k': 0.002,
                'max_tokens': 4096
            },
            'openai-gpt-4': {
                'name': 'GPT-4',
                'description': 'OpenAI GPT-4 Modell (Premium)',
                'available': bool(OPENAI_API_KEY),
                'cost_per_1k': 0.03,
                'max_tokens': 8192
            }
        }
    
    async def get_response(self, messages: List[Dict], model: str = 'ollama', **kwargs) -> str:
        """Get response from specified AI model"""
        if model not in self.models or not self.models[model]['available']:
            model = 'ollama'  # Fallback
        
        try:
            if model == 'ollama':
                return get_response_with_messages(messages)
            
            elif model.startswith('openai'):
                return await self._get_openai_response(messages, model, **kwargs)
            
            else:
                raise ValueError(f"Unknown model: {model}")
        
        except Exception as e:
            logger.error("AI model error", model=model, error=str(e))
            # Fallback to ollama
            if model != 'ollama':
                return await self.get_response(messages, 'ollama', **kwargs)
            raise
    
    async def _get_openai_response(self, messages: List[Dict], model: str, **kwargs) -> str:
        """Get response from OpenAI API"""
        model_name = "gpt-3.5-turbo" if "3.5" in model else "gpt-4"
        
        headers = {
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "Content-Type": "application/json"
        }
        
        data = {
            "model": model_name,
            "messages": messages,
            "max_tokens": kwargs.get('max_tokens', 1000),
            "temperature": kwargs.get('temperature', 0.7)
        }
        
        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers=headers,
                json=data,
                timeout=30
            )
            
            if response.status_code != 200:
                raise HTTPException(status_code=response.status_code, detail="OpenAI API error")
            
            result = response.json()
            return result["choices"][0]["message"]["content"]
    
    def get_available_models(self, subscription_tier: str = 'free') -> Dict:
        """Get available models based on subscription"""
        available = {}
        for model_id, model_info in self.models.items():
            if not model_info['available']:
                continue
            
            # Free users only get ollama
            if subscription_tier == 'free' and model_id != 'ollama':
                continue
            
            # Premium users get all except GPT-4
            if subscription_tier == 'premium' and model_id == 'openai-gpt-4':
                continue
            
            available[model_id] = model_info
        
        return available

ai_models = AIModelManager()

# ──────────────────────────────
# Enhanced Search System
# ──────────────────────────────
class ChatSearchEngine:
    def __init__(self):
        self.stop_words = {'der', 'die', 'und', 'in', 'den', 'von', 'zu', 'das', 'mit', 'sich', 'des', 'auf', 'für', 'ist', 'im', 'dem', 'nicht', 'ein', 'eine', 'als', 'auch', 'es', 'an', 'werden', 'aus', 'er', 'hat', 'dass', 'sie', 'nach', 'wird', 'bei', 'einer', 'um', 'am', 'sind', 'noch', 'wie', 'einem', 'über', 'einen', 'so', 'zum', 'war', 'haben', 'nur', 'oder', 'aber', 'vor', 'zur', 'bis', 'mehr', 'durch', 'man', 'sein', 'wurde', 'sei', 'seit'}
    
    def search_messages(self, username: str, query: str, limit: int = 50) -> List[Dict]:
        """Search through user's chat history"""
        query_terms = self._process_query(query)
        if not query_terms:
            return []
        
        with DatabaseConnection(db_pool) as conn:
            cursor = conn.cursor()
            
            # Build search query
            placeholders = ' OR '.join(['content LIKE ?' for _ in query_terms])
            like_terms = [f'%{term}%' for term in query_terms]
            
            cursor.execute(f"""
                SELECT role, content, timestamp, 
                       CASE 
                           WHEN {placeholders} THEN 1 
                           ELSE 0 
                       END as relevance
                FROM chat_history 
                WHERE username = ? AND ({placeholders})
                ORDER BY relevance DESC, timestamp DESC
                LIMIT ?
            """, [username] + like_terms + like_terms + [limit])
            
            results = []
            for row in cursor.fetchall():
                results.append({
                    'role': row['role'],
                    'content': row['content'],
                    'timestamp': row['timestamp'],
                    'relevance': row['relevance'],
                    'snippet': self._create_snippet(row['content'], query_terms)
                })
            
            return results
    
    def _process_query(self, query: str) -> List[str]:
        """Process search query into terms"""
        # Remove special characters and convert to lowercase
        query = re.sub(r'[^\w\s]', ' ', query.lower())
        terms = query.split()
        
        # Remove stop words and short terms
        terms = [term for term in terms if term not in self.stop_words and len(term) > 2]
        
        return terms[:10]  # Limit to 10 terms
    
    def _create_snippet(self, content: str, query_terms: List[str]) -> str:
        """Create a snippet highlighting query terms"""
        content_lower = content.lower()
        
        # Find first occurrence of any query term
        first_pos = len(content)
        for term in query_terms:
            pos = content_lower.find(term)
            if pos != -1 and pos < first_pos:
                first_pos = pos
        
        # Create snippet around first occurrence
        start = max(0, first_pos - 50)
        end = min(len(content), first_pos + 150)
        snippet = content[start:end]
        
        # Add ellipsis if truncated
        if start > 0:
            snippet = "..." + snippet
        if end < len(content):
            snippet = snippet + "..."
        
        # Highlight query terms
        for term in query_terms:
            pattern = re.compile(re.escape(term), re.IGNORECASE)
            snippet = pattern.sub(f'<mark>{term}</mark>', snippet)
        
        return snippet

search_engine = ChatSearchEngine()

# ──────────────────────────────
# Conversation Threading System
# ──────────────────────────────
def init_conversations_db():
    """Initialize conversation threading database"""
    with DatabaseConnection(db_pool) as conn:
        cursor = conn.cursor()
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS conversations (
                id TEXT PRIMARY KEY,
                username TEXT NOT NULL,
                title TEXT NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                last_activity DATETIME DEFAULT CURRENT_TIMESTAMP,
                message_count INTEGER DEFAULT 0,
                is_archived BOOLEAN DEFAULT 0,
                FOREIGN KEY (username) REFERENCES users (username)
            )
        """)
        
        # Add conversation_id to chat_history if not exists
        cursor.execute("PRAGMA table_info(chat_history)")
        columns = [column[1] for column in cursor.fetchall()]
        
        if 'conversation_id' not in columns:
            cursor.execute("ALTER TABLE chat_history ADD COLUMN conversation_id TEXT")
            # Create default conversation for existing messages
            cursor.execute("""
                INSERT OR IGNORE INTO conversations (id, username, title)
                SELECT DISTINCT 'default_' || username, username, 'Hauptunterhaltung'
                FROM chat_history
            """)
            # Assign existing messages to default conversation
            cursor.execute("""
                UPDATE chat_history SET conversation_id = 'default_' || username
                WHERE conversation_id IS NULL
            """)

def create_conversation(username: str, title: str = None) -> str:
    """Create a new conversation thread"""
    conversation_id = f"conv_{uuid.uuid4().hex[:12]}"
    title = title or f"Unterhaltung {datetime.now().strftime('%d.%m. %H:%M')}"
    
    with DatabaseConnection(db_pool) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO conversations (id, username, title)
            VALUES (?, ?, ?)
        """, (conversation_id, username, title))
    
    return conversation_id

def get_user_conversations(username: str) -> List[Dict]:
    """Get all conversations for a user"""
    with DatabaseConnection(db_pool) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, title, created_at, last_activity, message_count, is_archived
            FROM conversations
            WHERE username = ? AND is_archived = 0
            ORDER BY last_activity DESC
        """, (username,))
        
        return [dict(row) for row in cursor.fetchall()]

# ──────────────────────────────
# Performance Monitoring Middleware (FIX)
# ──────────────────────────────
class PerformanceMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        start_time = time_now()
        
        # WICHTIG: Erst Pipeline laufen lassen, dann Session lesen!
        response = await call_next(request)
        
        # Session sicher abfragen
        user = None
        if "session" in request.scope:
            try:
                user = request.session.get("username")
            except Exception:
                user = None
        
        duration = time_now() - start_time
        
        # Record performance metric
        metric = PerformanceMetrics(
            endpoint=str(request.url.path),
            method=request.method,
            duration=duration,
            status_code=response.status_code,
            timestamp=datetime.now().isoformat(),
            user=user
        )
        
        # Log slow requests
        if duration > 1.0:
            logger.warning("Slow request", 
                          endpoint=metric.endpoint, 
                          duration=duration,
                          user=user)
        
        performance_monitor.record_metric(metric)
        
        # Add performance headers
        response.headers["X-Response-Time"] = f"{duration:.3f}"
        
        return response

app.add_middleware(PerformanceMiddleware)

# ──────────────────────────────
# Subscription System
# ──────────────────────────────
class SubscriptionTier(Enum):
    FREE = "free"
    PREMIUM = "premium"
    ENTERPRISE = "enterprise"

# Persona-Definitionen
PERSONAS = {
    "standard": {
        "name": "Standard Assistent",
        "emoji": "🤖",
        "system_prompt": "Du bist ein hilfsfreundlicher KI-Assistent. Antworte auf Deutsch und sei sachlich aber freundlich."
    },
    "freundlich": {
        "name": "Freundlicher Helfer", 
        "emoji": "😊",
        "system_prompt": "Du bist ein sehr freundlicher und enthusiastischer Assistent. Verwende warme, ermutigende Worte und zeige echtes Interesse an den Fragen. Sei optimistisch und unterstützend."
    },
    "lustig": {
        "name": "Comedy Bot",
        "emoji": "😄", 
        "system_prompt": "Du bist ein humorvoller Assistent der gerne Witze macht und lustige Antworten gibt. Bleibe trotzdem hilfreich, aber bringe den User zum Lächeln. Verwende gelegentlich Wortwitz oder lustige Vergleiche."
    },
    "professionell": {
        "name": "Business Experte",
        "emoji": "👔",
        "system_prompt": "Du bist ein professioneller Berater mit Expertise in Business und Technik. Antworte präzise, strukturiert und sachlich. Nutze Fachbegriffe angemessen und gib konkrete Handlungsempfehlungen."
    },
    "lehrerin": {
        "name": "Geduldige Lehrerin", 
        "emoji": "👩‍🏫",
        "system_prompt": "Du bist eine geduldige Lehrerin die komplexe Themen einfach erklärt. Baue Erklärungen schrittweise auf, verwende Beispiele und frage nach ob alles verstanden wurde. Ermutige zum Lernen."
    },
    "kreativ": {
        "name": "Kreativer Geist",
        "emoji": "🎨", 
        "system_prompt": "Du bist ein kreativer Assistent voller Ideen und Inspiration. Denke um die Ecke, schlage ungewöhnliche Lösungen vor und bringe künstlerische Perspektiven ein. Sei experimentierfreudig."
    }
}

SUBSCRIPTION_LIMITS = {
    SubscriptionTier.FREE: {
        "messages_per_day": 50,
        "api_calls_per_day": 100,
        "personas": ["standard", "freundlich", "lustig"],
        "features": ["basic_chat", "history"],
        "max_context_length": 5
    },
    SubscriptionTier.PREMIUM: {
        "messages_per_day": 500,
        "api_calls_per_day": 1000,
        "personas": list(PERSONAS.keys()),
        "features": ["basic_chat", "history", "tts", "export", "advanced_personas", "markdown", "file_upload"],
        "max_context_length": 20
    },
    SubscriptionTier.ENTERPRISE: {
        "messages_per_day": -1,
        "api_calls_per_day": -1,
        "personas": list(PERSONAS.keys()),
        "features": ["all"],
        "max_context_length": 50
    }
}

# ──────────────────────────────
# Database Helper Functions
# ──────────────────────────────
def init_db():
    """Initialize database with all necessary tables"""
    global CHAT_TABLE_CREATED
    
    with DatabaseConnection(db_pool) as conn:
        cursor = conn.cursor()
        
        # Users table (extended with subscription and theme)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                username TEXT PRIMARY KEY,
                password TEXT NOT NULL,
                question TEXT NOT NULL,
                answer TEXT NOT NULL,
                is_admin INTEGER DEFAULT 0,
                is_blocked INTEGER DEFAULT 0,
                persona TEXT DEFAULT 'standard',
                subscription TEXT DEFAULT 'free',
                theme TEXT DEFAULT 'light'
            )
        """)
        
        # Chat history table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS chat_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                conversation_id TEXT,
                FOREIGN KEY (username) REFERENCES users (username)
            )
        """)
        
        # API Keys table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS api_keys (
                api_key TEXT PRIMARY KEY,
                username TEXT NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                requests_today INTEGER DEFAULT 0,
                total_requests INTEGER DEFAULT 0,
                last_used DATETIME DEFAULT CURRENT_TIMESTAMP,
                is_active BOOLEAN DEFAULT 1,
                FOREIGN KEY (username) REFERENCES users (username)
            )
        """)
        
        # Sessions table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY,
                username TEXT NOT NULL,
                data TEXT NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                last_activity DATETIME DEFAULT CURRENT_TIMESTAMP,
                expires_at DATETIME NOT NULL
            )
        """)
        
        # Admin user creation
        cursor.execute("SELECT username FROM users WHERE username = ?", ("admin",))
        if not cursor.fetchone():
            admin_hash = hash_password("admin")
            cursor.execute("""
                INSERT INTO users (username, password, question, answer, is_admin, subscription) 
                VALUES (?, ?, ?, ?, 1, 'enterprise')
            """, ("admin", admin_hash, "Default Admin Question", "admin"))
            logger.info("[INIT] Admin user created (admin/admin)")
    
    CHAT_TABLE_CREATED = True
    logger.info("[INIT] Database initialized")

def hash_password(password: str) -> str:
    """Hash password"""
    return hashlib.sha256(password.encode()).hexdigest()

def generate_csrf_token() -> str:
    """Generate CSRF token"""
    return secrets.token_urlsafe(32)

def verify_csrf_token(token: str, session_token: str) -> bool:
    """Verify CSRF token"""
    return hmac.compare_digest(token, session_token)

def render_markdown_simple(text: str) -> str:
    """Simple markdown rendering for chat messages"""
    text = html.escape(text)
    
    # Code blocks (```code```)
    text = re.sub(
        r'```(\w+)?\n?(.*?)```', 
        r'<div class="code-block"><div class="code-header">\1</div><pre><code>\2</code></pre></div>', 
        text, 
        flags=re.DOTALL
    )
    
    # Inline code (`code`)
    text = re.sub(r'`([^`]+)`', r'<code class="inline-code">\1</code>', text)
    
    # Bold (**text**)
    text = re.sub(r'\*\*([^*]+)\*\*', r'<strong>\1</strong>', text)
    
    # Italic (*text*)
    text = re.sub(r'\*([^*]+)\*', r'<em>\1</em>', text)
    
    # Lists (- item)
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
    
    # Line breaks
    text = text.replace('\n', '<br>')
    
    return text

def get_user(username: str) -> dict:
    """Get user from database"""
    cache_key = f"user_{username}"
    cached_user = cache.get(cache_key)
    if cached_user:
        return cached_user
    
    with DatabaseConnection(db_pool) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE username = ?", (username,))
        row = cursor.fetchone()
    
    if row:
        user_data = {
            "username": row[0],
            "password": row[1],
            "question": row[2],
            "answer": row[3],
            "is_admin": bool(row[4]),
            "is_blocked": bool(row[5]),
            "persona": row[6] if len(row) > 6 else "standard",
            "subscription": row[7] if len(row) > 7 else "free",
            "theme": row[8] if len(row) > 8 else "light"
        }
        cache.set(cache_key, user_data)
        return user_data
    
    return None

def get_all_users() -> dict:
    """Get all users for admin panel"""
    with DatabaseConnection(db_pool) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users")
        rows = cursor.fetchall()
    
    users = {}
    for row in rows:
        users[row[0]] = {
            "password": row[1],
            "question": row[2],
            "answer": row[3],
            "is_admin": bool(row[4]),
            "blocked": bool(row[5]),
            "subscription": row[7] if len(row) > 7 else "free",
            "theme": row[8] if len(row) > 8 else "light"
        }
    return users

def save_user(username: str, password: str, question: str, answer: str) -> bool:
    """Register new user"""
    if get_user(username):
        return False
    
    with DatabaseConnection(db_pool) as conn:
        cursor = conn.cursor()
        try:
            password_hash = hash_password(password)
            cursor.execute("""
                INSERT INTO users (username, password, question, answer) 
                VALUES (?, ?, ?, ?)
            """, (username, password_hash, question, answer))
            logger.info(f"[REGISTER] New user: {username}")
            return True
        except sqlite3.IntegrityError:
            return False

def check_login(username: str, password: str) -> bool:
    """Check login credentials"""
    user = get_user(username)
    if user:
        return user["password"] == hash_password(password)
    return False

def verify_security_answer(username: str, answer: str) -> bool:
    """Verify security answer"""
    user = get_user(username)
    if user:
        return user["answer"] == answer
    return False

def reset_password(username: str, new_password: str):
    """Reset password"""
    with DatabaseConnection(db_pool) as conn:
        cursor = conn.cursor()
        password_hash = hash_password(new_password)
        cursor.execute("UPDATE users SET password = ? WHERE username = ?", 
                       (password_hash, username))
    
    # Clear user from cache
    cache.delete(f"user_{username}")

def get_user_history(username: str, conversation_id: str = None) -> list:
    """Get chat history from database"""
    if not CHAT_TABLE_CREATED:
        return []
    
    cache_key = f"history_{username}_{conversation_id or 'default'}"
    cached_history = cache.get(cache_key)
    if cached_history:
        return cached_history
    
    with DatabaseConnection(db_pool) as conn:
        cursor = conn.cursor()
        if conversation_id:
            cursor.execute("""
                SELECT role, content, timestamp FROM chat_history 
                WHERE username = ? AND conversation_id = ?
                ORDER BY timestamp ASC
            """, (username, conversation_id))
        else:
            cursor.execute("""
                SELECT role, content, timestamp FROM chat_history 
                WHERE username = ? ORDER BY timestamp ASC
            """, (username,))
        
        rows = cursor.fetchall()
    
    history = [{"role": row[0], "content": row[1], "timestamp": row[2]} for row in rows]
    cache.set(cache_key, history)  # FIX: kein drittes Argument
    return history

def save_user_history(username: str, role: str, content: str, conversation_id: str = None):
    """Save chat message to database"""
    if not CHAT_TABLE_CREATED:
        return
    
    with DatabaseConnection(db_pool) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO chat_history (username, role, content, conversation_id) 
            VALUES (?, ?, ?, ?)
        """, (username, role, content, conversation_id))
    
    # Clear cache
    cache.delete(f"history_{username}_{conversation_id or 'default'}")

def delete_user_history(username: str):
    """Delete chat history"""
    if not CHAT_TABLE_CREATED:
        return
    
    with DatabaseConnection(db_pool) as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM chat_history WHERE username = ?", (username,))
    
    # Clear cache
    cache.delete(f"history_{username}_default")

def get_user_subscription(username: str) -> SubscriptionTier:
    """Get user subscription status"""
    user = get_user(username)
    if user:
        subscription = user.get("subscription", "free")
        try:
            return SubscriptionTier(subscription)
        except ValueError:
            return SubscriptionTier.FREE
    return SubscriptionTier.FREE

def set_user_subscription(username: str, tier: SubscriptionTier):
    """Set subscription tier for user"""
    with DatabaseConnection(db_pool) as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET subscription = ? WHERE username = ?", (tier.value, username))
    
    cache.delete(f"user_{username}")

def get_user_theme(username: str) -> str:
    """Get user theme preference"""
    user = get_user(username)
    return user.get("theme", "light") if user else "light"

def set_user_theme(username: str, theme: str):
    """Set user theme preference"""
    with DatabaseConnection(db_pool) as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET theme = ? WHERE username = ?", (theme, username))
    
    cache.delete(f"user_{username}")

def get_user_persona(username: str) -> str:
    """Get user's selected persona"""
    user = get_user(username)
    return user.get("persona", "standard") if user else "standard"

def save_user_persona(username: str, persona: str):
    """Save user's selected persona"""
    with DatabaseConnection(db_pool) as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET persona = ? WHERE username = ?", (persona, username))
    
    cache.delete(f"user_{username}")

def check_feature_access(username: str, feature: str) -> bool:
    """Check if user has access to feature"""
    tier = get_user_subscription(username)
    allowed_features = SUBSCRIPTION_LIMITS[tier]["features"]
    
    return "all" in allowed_features or feature in allowed_features

def check_daily_limit(username: str, limit_type: str) -> bool:
    """Check daily limits (messages, API calls)"""
    tier = get_user_subscription(username)
    limit = SUBSCRIPTION_LIMITS[tier].get(f"{limit_type}_per_day", 0)
    
    if limit == -1:  # Unlimited
        return True
    
    # Check current usage
    analytics = load_analytics()
    today = datetime.now().strftime("%Y-%m-%d")
    user_data = analytics["users"].get(username, {})
    
    current_usage = user_data.get(f"{limit_type}_today", 0)
    return current_usage < limit

def get_available_personas(username: str) -> dict:
    """Get available personas based on subscription"""
    tier = get_user_subscription(username)
    allowed_personas = SUBSCRIPTION_LIMITS[tier]["personas"]
    
    return {key: value for key, value in PERSONAS.items() if key in allowed_personas}

def get_response_with_context(current_message: str, chat_history: list, persona: str = "standard") -> str:
    """Get AI response with chat context and persona"""
    messages = []
    
    # System prompt based on selected persona
    persona_config = PERSONAS.get(persona, PERSONAS["standard"])
    messages.append({
        "role": "system", 
        "content": persona_config["system_prompt"]
    })
    
    # Add chat history (last 20 messages for better context)
    for msg in chat_history[-20:]:
        if msg["role"] in ["user", "assistant"]:
            messages.append({
                "role": msg["role"],
                "content": msg["content"]
            })
    
    # Add current message
    messages.append({
        "role": "user",
        "content": current_message
    })
    
    return get_response_with_messages(messages)

# ──────────────────────────────
# API Key Management (Database)
# ──────────────────────────────
def save_api_key_to_db(api_key: str, username: str):
    """Save API key to database"""
    with DatabaseConnection(db_pool) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO api_keys (api_key, username)
            VALUES (?, ?)
        """, (api_key, username))

def get_api_key_from_db(api_key: str) -> Optional[dict]:
    """Get API key info from database"""
    with DatabaseConnection(db_pool) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT username, created_at, requests_today, total_requests, last_used
            FROM api_keys 
            WHERE api_key = ? AND is_active = 1
        """, (api_key,))
        
        row = cursor.fetchone()
        if row:
            return {
                "username": row[0],
                "created_at": row[1],
                "requests_today": row[2],
                "total_requests": row[3],
                "last_used": row[4]
            }
    return None

def get_user_api_keys(username: str) -> List[dict]:
    """Get all API keys for a user"""
    with DatabaseConnection(db_pool) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT api_key, created_at, requests_today, total_requests, last_used
            FROM api_keys 
            WHERE username = ? AND is_active = 1
            ORDER BY created_at DESC
        """, (username,))
        
        return [dict(row) for row in cursor.fetchall()]

def update_api_key_usage(api_key: str):
    """Update API key usage statistics"""
    with DatabaseConnection(db_pool) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE api_keys 
            SET requests_today = requests_today + 1, 
                total_requests = total_requests + 1,
                last_used = datetime('now')
            WHERE api_key = ?
        """, (api_key,))
# ──────────────────────────────
# Analytics System
# ──────────────────────────────
def load_analytics():
    """Load analytics data"""
    if os.path.exists(ANALYTICS_FILE):
        try:
            with open(ANALYTICS_FILE, 'r') as f:
                return json.load(f)
        except:
            return {"daily": {}, "users": {}, "features": {}, "api": {}}
    return {"daily": {}, "users": {}, "features": {}, "api": {}}

def save_analytics(data):
    """Save analytics data"""
    with open(ANALYTICS_FILE, 'w') as f:
        json.dump(data, f, indent=2)

def track_user_action(username: str, action: str, details: dict = None):
    """Track user actions for analytics"""
    background_tasks.add_task(_track_user_action_async, username, action, details)

def _track_user_action_async(username: str, action: str, details: dict = None):
    """Async analytics tracking"""
    analytics = load_analytics()
    today = datetime.now().strftime("%Y-%m-%d")
    
    # Daily statistics
    if today not in analytics["daily"]:
        analytics["daily"][today] = {
            "total_messages": 0,
            "unique_users": [],
            "personas_used": {},
            "features_used": {}
        }
    
    # User-specific statistics
    if username not in analytics["users"]:
        analytics["users"][username] = {
            "first_seen": today,
            "last_seen": today,
            "total_messages": 0,
            "messages_today": 0,
            "api_calls_today": 0,
            "favorite_persona": "standard",
            "features_used": []
        }
    
    # Reset daily counters if new day
    if analytics["users"][username].get("last_reset") != today:
        analytics["users"][username]["messages_today"] = 0
        analytics["users"][username]["api_calls_today"] = 0
        analytics["users"][username]["last_reset"] = today
    
    # Update data
    if action == "chat_message":
        analytics["daily"][today]["total_messages"] += 1
        if username not in analytics["daily"][today]["unique_users"]:
            analytics["daily"][today]["unique_users"].append(username)
        analytics["users"][username]["total_messages"] += 1
        analytics["users"][username]["messages_today"] += 1
        analytics["users"][username]["last_seen"] = today
        
        if details and "persona" in details:
            persona = details["persona"]
            analytics["daily"][today]["personas_used"][persona] = analytics["daily"][today]["personas_used"].get(persona, 0) + 1
    
    elif action == "api_call":
        analytics["users"][username]["api_calls_today"] += 1
        if details and "persona" in details:
            persona = details["persona"]
            analytics["daily"][today]["personas_used"][persona] = analytics["daily"][today]["personas_used"].get(persona, 0) + 1
    
    elif action == "feature_used":
        feature = details.get("feature", "unknown") if details else "unknown"
        analytics["daily"][today]["features_used"][feature] = analytics["daily"][today]["features_used"].get(feature, 0) + 1
        if feature not in analytics["users"][username]["features_used"]:
            analytics["users"][username]["features_used"].append(feature)
    
    save_analytics(analytics)

# ──────────────────────────────
# Rate Limiting & Session Management
# ──────────────────────────────
def load_rate_limits():
    """Load rate limit data"""
    if os.path.exists(RATE_LIMIT_FILE):
        try:
            with open(RATE_LIMIT_FILE, 'r') as f:
                return json.load(f)
        except:
            return {}
    return {}

def save_rate_limits(limits):
    """Save rate limit data"""
    with open(RATE_LIMIT_FILE, 'w') as f:
        json.dump(limits, f)

def check_rate_limit(username: str) -> bool:
    """Check if user has reached rate limit"""
    limits = load_rate_limits()
    current_time = time_now()
    
    user_data = limits.get(username, {"messages": [], "last_reset": current_time})
    
    # Remove messages older than 1 hour
    hour_ago = current_time - 3600
    user_data["messages"] = [msg_time for msg_time in user_data["messages"] if msg_time > hour_ago]
    
    # Check if limit reached
    if len(user_data["messages"]) >= MESSAGES_PER_HOUR:
        return False
    
    # Add new message
    user_data["messages"].append(current_time)
    limits[username] = user_data
    save_rate_limits(limits)
    
    return True

def update_session_activity(request: Request):
    """Update last activity in session"""
    request.session["last_activity"] = time_now()

def check_session_timeout(request: Request) -> bool:
    """Check if session has timed out"""
    last_activity = request.session.get("last_activity")
    if not last_activity:
        return True
    
    return (time_now() - last_activity) > SESSION_TIMEOUT_SECONDS

def require_active_session(request: Request):
    """Check for active session"""
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

def toggle_user_block(username: str):
    """Toggle user block status"""
    with DatabaseConnection(db_pool) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT is_blocked FROM users WHERE username = ?", (username,))
        current = cursor.fetchone()[0]
        new_status = 0 if current else 1
        cursor.execute("UPDATE users SET is_blocked = ? WHERE username = ?", 
                       (new_status, username))
    
    cache.delete(f"user_{username}")

def delete_user_completely(username: str):
    """Delete user and all data"""
    with DatabaseConnection(db_pool) as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM users WHERE username = ?", (username,))
        cursor.execute("DELETE FROM chat_history WHERE username = ?", (username,))
        cursor.execute("DELETE FROM api_keys WHERE username = ?", (username,))
    
    cache.delete(f"user_{username}")

# ──────────────────────────────
# Export Functions
# ──────────────────────────────
def export_user_chat_txt(username: str) -> str:
    """Export chat history as text"""
    history = get_user_history(username)
    output = [f"Chat-Export für {username} - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"]
    output.append("=" * 60 + "\n")
    
    for msg in history:
        timestamp = msg.get('timestamp', '')
        role = msg.get('role', '').upper()
        content = msg.get('content', '')
        
        output.append(f"[{timestamp}] {role}:\n{content}\n\n")
    
    return '\n'.join(output)

def export_user_chat_json(username: str) -> dict:
    """Export chat history as JSON"""
    history = get_user_history(username)
    user = get_user(username)
    
    return {
        "export_date": datetime.now().isoformat(),
        "username": username,
        "user_info": {
            "subscription": user.get("subscription", "free"),
            "persona": user.get("persona", "standard"),
            "theme": user.get("theme", "light")
        },
        "chat_history": history
    }

def create_user_export_package(username: str) -> bytes:
    """Create ZIP package with all user data"""
    zip_buffer = io.BytesIO()
    
    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
        # Chat history as text
        txt_content = export_user_chat_txt(username)
        zip_file.writestr(f"{username}_chat.txt", txt_content.encode('utf-8'))
        
        # Chat history as JSON
        json_content = json.dumps(export_user_chat_json(username), indent=2, ensure_ascii=False)
        zip_file.writestr(f"{username}_data.json", json_content.encode('utf-8'))
        
        # User info
        user = get_user(username)
        user_info = {
            "username": username,
            "subscription": user.get("subscription", "free"),
            "persona": user.get("persona", "standard"),
            "theme": user.get("theme", "light"),
            "export_date": datetime.now().isoformat()
        }
        zip_file.writestr("user_info.json", json.dumps(user_info, indent=2).encode('utf-8'))
    
    zip_buffer.seek(0)
    return zip_buffer.read()

# ──────────────────────────────
# Validation Helper
# ──────────────────────────────
def validate_password_strength(password: str) -> tuple[bool, str]:
    """Validate password strength"""
    if len(password) < 8:
        return False, "Passwort muss mindestens 8 Zeichen haben"
    
    if not any(c.isupper() for c in password):
        return False, "Passwort muss mindestens einen Großbuchstaben enthalten"
    
    if not any(c.islower() for c in password):
        return False, "Passwort muss mindestens einen Kleinbuchstaben enthalten"
    
    if not any(c.isdigit() for c in password):
        return False, "Passwort muss mindestens eine Zahl enthalten"
    
    return True, "Passwort ist sicher"

def validate_username(username: str) -> tuple[bool, str]:
    """Validate username"""
    if len(username) < 3:
        return False, "Benutzername muss mindestens 3 Zeichen haben"
    
    if len(username) > 20:
        return False, "Benutzername darf maximal 20 Zeichen haben"
    
    if not username.replace('_', '').replace('-', '').isalnum():
        return False, "Benutzername darf nur Buchstaben, Zahlen, _ und - enthalten"
    
    return True, "Benutzername ist gültig"

# ──────────────────────────────
# Health Check System
# ──────────────────────────────
@app.get("/health")
async def health_check():
    """Comprehensive health check endpoint"""
    health_data = {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "version": "2.0.0",
        "checks": {}
    }
    
    # Database check
    try:
        with DatabaseConnection(db_pool) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT 1")
            health_data["checks"]["database"] = "healthy"
    except Exception as e:
        health_data["checks"]["database"] = f"unhealthy: {str(e)}"
        health_data["status"] = "degraded"
    
    # File system check
    try:
        test_file = os.path.join(UPLOAD_DIR, ".health_check")
        with open(test_file, "w") as f:
            f.write("test")
        os.remove(test_file)
        health_data["checks"]["filesystem"] = "healthy"
    except Exception as e:
        health_data["checks"]["filesystem"] = f"unhealthy: {str(e)}"
        health_data["status"] = "degraded"
    
    # AI Model check
    try:
        test_messages = [{"role": "user", "content": "test"}]
        await ai_models.get_response(test_messages)
        health_data["checks"]["ai_model"] = "healthy"
    except Exception as e:
        health_data["checks"]["ai_model"] = f"unhealthy: {str(e)}"
        health_data["status"] = "degraded"
    
    # Memory usage (psutil guard)
    if PSUTIL_AVAILABLE:
        process = psutil.Process()
        health_data["system"] = {
            "memory_usage_mb": round(process.memory_info().rss / 1024 / 1024, 2),
            "cpu_percent": process.cpu_percent(interval=0.1),
            "open_files": len(process.open_files())
        }
    else:
        health_data["system"] = {"note": "psutil not installed"}
    
    # Cache statistics
    health_data["cache"] = cache.stats()
    
    # Performance metrics
    health_data["performance"] = performance_monitor.get_stats()
    
    return health_data

# ──────────────────────────────
# Routes - Auth
# ──────────────────────────────
@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    request.session.clear()
    timeout = request.query_params.get("timeout")
    return templates.TemplateResponse("login.html", {
        "request": request,
        "timeout_message": "Deine Session ist abgelaufen. Bitte melde dich erneut an." if timeout else None
    })

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
        request.session["csrf_token"] = generate_csrf_token()
        update_session_activity(request)
        
        if user["is_admin"]:
            return RedirectResponse("/admin", status_code=302)
        else:
            return RedirectResponse("/chat", status_code=302)
    
    return templates.TemplateResponse("login.html", {
        "request": request, 
        "error": "Falsche Anmeldedaten"
    })
# ──────────────────────────────
# Routes - Admin
# ──────────────────────────────
from jinja2 import TemplateNotFound

def _require_admin(request: Request):
    """Gemeinsame Guard-Checks für Admin-Seiten."""
    redirect = require_active_session(request)
    if redirect:
        return redirect
    redirect = admin_redirect_guard(request)
    if redirect:
        return redirect
    return None

@app.get("/admin", response_class=HTMLResponse)
async def admin_dashboard(request: Request):
    guard = _require_admin(request)
    if guard:
        return guard

    users = get_all_users()
    csrf_token = request.session.get("csrf_token", "")

    # Versuche Template; wenn nicht vorhanden → Fallback HTML
    tpl_path = os.path.join("templates", "admin.html")
    if os.path.exists(tpl_path):
        return templates.TemplateResponse("admin.html", {
            "request": request,
            "users": users,
            "csrf_token": csrf_token
        })
    else:
        # Einfache Fallback-Seite, damit /admin ohne Template funktioniert
        rows = []
        for uname, info in users.items():
            rows.append(f"""
            <tr>
              <td>{html.escape(uname)}</td>
              <td>{'✅' if info.get('is_admin', False) else '—'}</td>
              <td>{'🚫 gesperrt' if info.get('blocked') else 'ok'}</td>
              <td>{html.escape(info.get('subscription','free'))}</td>
              <td style="display:flex;gap:8px;">
                <form method="post" action="/admin/users/toggle-block">
                  <input type="hidden" name="csrf_token" value="{csrf_token}">
                  <input type="hidden" name="username" value="{html.escape(uname)}">
                  <button type="submit">{'Entsperren' if info.get('blocked') else 'Sperren'}</button>
                </form>
                <form method="post" action="/admin/users/delete" onsubmit="return confirm('User wirklich löschen?')">
                  <input type="hidden" name="csrf_token" value="{csrf_token}">
                  <input type="hidden" name="username" value="{html.escape(uname)}">
                  <button type="submit">Löschen</button>
                </form>
                <a href="/admin/export/{html.escape(uname)}">Export</a>
              </td>
            </tr>
            """)
        html_page = f"""
        <html>
          <head>
            <meta charset="utf-8">
            <title>Admin Dashboard</title>
            <style>
              body {{ font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif; padding: 24px; }}
              table {{ border-collapse: collapse; width: 100%; }}
              th, td {{ border: 1px solid #ddd; padding: 8px; }}
              th {{ background: #f5f5f5; text-align: left; }}
              form {{ display: inline; }}
              button {{ cursor: pointer; }}
            </style>
          </head>
          <body>
            <h1>Admin Dashboard</h1>
            <p>CSRF Token: <code>{csrf_token}</code></p>
            <table>
              <thead>
                <tr>
                  <th>Benutzername</th>
                  <th>Admin</th>
                  <th>Status</th>
                  <th>Subscription</th>
                  <th>Aktionen</th>
                </tr>
              </thead>
              <tbody>
                {''.join(rows)}
              </tbody>
            </table>
          </body>
        </html>
        """
        return HTMLResponse(content=html_page, status_code=200)

@app.post("/admin/users/toggle-block")
async def admin_toggle_block(request: Request, username: str = Form(...), csrf_token: str = Form(...)):
    guard = _require_admin(request)
    if guard:
        return guard

    if not verify_csrf_token(csrf_token, request.session.get("csrf_token", "")):
        raise HTTPException(status_code=403, detail="Ungültiges CSRF-Token")

    if username == "admin":
        raise HTTPException(status_code=400, detail="Admin-Account kann nicht gesperrt werden")

    toggle_user_block(username)
    return RedirectResponse("/admin", status_code=302)

@app.post("/admin/users/delete")
async def admin_delete_user(request: Request, username: str = Form(...), csrf_token: str = Form(...)):
    guard = _require_admin(request)
    if guard:
        return guard

    if not verify_csrf_token(csrf_token, request.session.get("csrf_token", "")):
        raise HTTPException(status_code=403, detail="Ungültiges CSRF-Token")

    if username == "admin":
        raise HTTPException(status_code=400, detail="Admin-Account kann nicht gelöscht werden")

    delete_user_completely(username)
    return RedirectResponse("/admin", status_code=302)

@app.get("/admin/export/{username}")
async def admin_export_user(request: Request, username: str):
    guard = _require_admin(request)
    if guard:
        return guard

    # Erstelle ZIP mit allen Userdaten
    data = create_user_export_package(username)
    fname = f"{username}_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
    return Response(
        content=data,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{fname}"'
        }
    )


# ──────────────────────────────
# Startup: init DBs (CRITICAL)
# ──────────────────────────────
# Wichtig: erst NACH Definition von DatabaseConnection und Funktionen aufrufen
init_db()
init_conversations_db()
