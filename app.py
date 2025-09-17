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
import asyncio
from typing import Dict, List, Callable, Any, Optional
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
import traceback
import uuid

class TaskStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

@dataclass
class TaskInfo:
    """Info über einen Background-Task"""
    id: str
    name: str
    status: TaskStatus
    created_at: datetime
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    error_message: Optional[str] = None
    result: Any = None
    progress: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)

class BackgroundTaskManager:
    """
    Manager für Background-Tasks
    - Registriert und verwaltet Tasks
    - Überwacht Status und Ergebnisse
    - Bietet Admin-Interface für Monitoring
    """
    
    def __init__(self, max_concurrent_tasks: int = 5):
        self.tasks: Dict[str, TaskInfo] = {}
        self.running_tasks: Dict[str, asyncio.Task] = {}
        self.max_concurrent = max_concurrent_tasks
        self.semaphore = asyncio.Semaphore(max_concurrent_tasks)
        self.registered_task_functions: Dict[str, Callable] = {}
        
        # Periodische Tasks
        self.scheduled_tasks: Dict[str, Dict] = {}
        self.scheduler_running = False

    def register_task_function(self, name: str, func: Callable, description: str = ""):
        """Registriert eine Task-Funktion"""
        self.registered_task_functions[name] = {
            'function': func,
            'description': description
        }

    async def run_task(self, task_name: str, func: Callable, *args, **kwargs) -> str:
        """Startet einen Background-Task"""
        task_id = str(uuid.uuid4())[:8]
        
        # Task-Info erstellen
        task_info = TaskInfo(
            id=task_id,
            name=task_name,
            status=TaskStatus.PENDING,
            created_at=datetime.now()
        )
        
        self.tasks[task_id] = task_info
        
        # Task starten
        async_task = asyncio.create_task(self._execute_task(task_id, func, *args, **kwargs))
        self.running_tasks[task_id] = async_task
        
        return task_id

    async def _execute_task(self, task_id: str, func: Callable, *args, **kwargs):
        """Führt einen Task aus"""
        task_info = self.tasks[task_id]
        
        try:
            async with self.semaphore:  # Begrenzt gleichzeitige Tasks
                task_info.status = TaskStatus.RUNNING
                task_info.started_at = datetime.now()
                
                # Task ausführen
                if asyncio.iscoroutinefunction(func):
                    result = await func(*args, **kwargs)
                else:
                    result = func(*args, **kwargs)
                
                # Task erfolgreich
                task_info.status = TaskStatus.COMPLETED
                task_info.completed_at = datetime.now()
                task_info.result = result
                task_info.progress = 100.0
                
        except asyncio.CancelledError:
            task_info.status = TaskStatus.CANCELLED
            task_info.completed_at = datetime.now()
            
        except Exception as e:
            task_info.status = TaskStatus.FAILED
            task_info.completed_at = datetime.now()
            task_info.error_message = str(e)
            task_info.metadata['traceback'] = traceback.format_exc()
            
        finally:
            # Task aus running_tasks entfernen
            self.running_tasks.pop(task_id, None)

    def cancel_task(self, task_id: str) -> bool:
        """Bricht einen laufenden Task ab"""
        if task_id in self.running_tasks:
            self.running_tasks[task_id].cancel()
            return True
        return False

    def get_task_info(self, task_id: str) -> Optional[TaskInfo]:
        """Holt Task-Informationen"""
        return self.tasks.get(task_id)

    def get_all_tasks(self) -> List[TaskInfo]:
        """Alle Tasks mit Status"""
        return list(self.tasks.values())

    def get_running_tasks(self) -> List[TaskInfo]:
        """Nur laufende Tasks"""
        return [info for info in self.tasks.values() if info.status == TaskStatus.RUNNING]

    def cleanup_old_tasks(self, older_than_hours: int = 24):
        """Räumt alte Task-Einträge auf"""
        cutoff_time = datetime.now() - timedelta(hours=older_than_hours)
        
        tasks_to_remove = []
        for task_id, task_info in self.tasks.items():
            if (task_info.status in [TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED] and
                task_info.completed_at and task_info.completed_at < cutoff_time):
                tasks_to_remove.append(task_id)
        
        for task_id in tasks_to_remove:
            del self.tasks[task_id]
        
        return len(tasks_to_remove)

    def schedule_recurring_task(self, name: str, func: Callable, interval_seconds: int, 
                              run_immediately: bool = False, **kwargs):
        """Plant einen wiederkehrenden Task"""
        self.scheduled_tasks[name] = {
            'function': func,
            'interval': interval_seconds,
            'next_run': datetime.now() if run_immediately else datetime.now() + timedelta(seconds=interval_seconds),
            'kwargs': kwargs,
            'last_run': None,
            'run_count': 0
        }
        
        # Scheduler starten falls nicht schon aktiv
        if not self.scheduler_running:
            asyncio.create_task(self._scheduler_loop())

    async def _scheduler_loop(self):
        """Scheduler-Loop für wiederkehrende Tasks"""
        self.scheduler_running = True
        
        while self.scheduler_running:
            try:
                current_time = datetime.now()
                
                for name, schedule_info in self.scheduled_tasks.items():
                    if current_time >= schedule_info['next_run']:
                        # Task starten
                        await self.run_task(
                            f"{name} (scheduled)", 
                            schedule_info['function'],
                            **schedule_info['kwargs']
                        )
                        
                        # Nächsten Lauf planen
                        schedule_info['last_run'] = current_time
                        schedule_info['next_run'] = current_time + timedelta(seconds=schedule_info['interval'])
                        schedule_info['run_count'] += 1
                
                await asyncio.sleep(30)  # Alle 30 Sekunden prüfen
                
            except Exception as e:
                logger.error(f"Scheduler error: {e}")
                await asyncio.sleep(60)  # Bei Fehler länger warten

    def get_stats(self) -> Dict:
        """Task-Manager Statistiken"""
        total_tasks = len(self.tasks)
        running = len([t for t in self.tasks.values() if t.status == TaskStatus.RUNNING])
        completed = len([t for t in self.tasks.values() if t.status == TaskStatus.COMPLETED])
        failed = len([t for t in self.tasks.values() if t.status == TaskStatus.FAILED])
        cancelled = len([t for t in self.tasks.values() if t.status == TaskStatus.CANCELLED])
        
        return {
            'total_tasks': total_tasks,
            'running_tasks': running,
            'completed_tasks': completed,
            'failed_tasks': failed,
            'cancelled_tasks': cancelled,
            'max_concurrent': self.max_concurrent,
            'scheduled_tasks': len(self.scheduled_tasks),
            'scheduler_active': self.scheduler_running
        }

# ──────────────────────────────
# Spezifische Background Task Funktionen
# ──────────────────────────────

async def enhanced_cache_cleanup():
    """Erweiterte Cache-Bereinigung als Background-Task"""
    try:
        cleaned_count = app_cache.cleanup_expired()
        
        # Cache-Statistiken sammeln
        stats_before = app_cache.stats()
        
        # Weitere Bereinigung wenn nötig
        if stats_before['total_entries'] > 1000:
            # Alte User-Cache-Einträge löschen
            old_keys = [key for key in stats_before['cache_keys'] 
                       if 'user:' in key or 'user_persona:' in key]
            
            for key in old_keys[:100]:  # Maximal 100 auf einmal
                app_cache.delete(key)
        
        stats_after = app_cache.stats()
        
        return {
            'cleaned_expired': cleaned_count,
            'cache_entries_before': stats_before['total_entries'],
            'cache_entries_after': stats_after['total_entries'],
            'memory_freed_mb': stats_before['memory_usage_mb'] - stats_after['memory_usage_mb']
        }
        
    except Exception as e:
        logger.error(f"Cache cleanup task error: {e}")
        raise

async def database_maintenance():
    """Datenbank-Wartung als Background-Task"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        results = {}
        
        # VACUUM für Defragmentierung
        cursor.execute("VACUUM")
        results['vacuum_completed'] = True
        
        # Analyze für Statistiken
        cursor.execute("ANALYZE")
        results['analyze_completed'] = True
        
        # Alte Chat-Nachrichten bereinigen (älter als 90 Tage)
        cursor.execute("""
            DELETE FROM chat_history 
            WHERE timestamp < datetime('now', '-90 days')
        """)
        deleted_messages = cursor.rowcount
        results['deleted_old_messages'] = deleted_messages
        
        # Datenbankgröße ermitteln
        db_size = os.path.getsize(DB_PATH) if os.path.exists(DB_PATH) else 0
        results['database_size_mb'] = round(db_size / (1024 * 1024), 2)
        
        conn.commit()
        conn.close()
        
        return results
        
    except Exception as e:
        logger.error(f"Database maintenance task error: {e}")
        raise

async def rate_limit_cleanup():
    """Rate-Limit-Daten bereinigen"""
    try:
        limits = load_rate_limits()
        initial_users = len(limits)
        
        current_time = _pytime.time()
        day_ago = current_time - 86400  # 24 Stunden
        
        cleaned_users = 0
        for username, user_data in list(limits.items()):
            # Alte Nachrichten entfernen
            if 'messages' in user_data:
                old_count = len(user_data['messages'])
                user_data['messages'] = [
                    msg_time for msg_time in user_data['messages'] 
                    if msg_time > day_ago
                ]
                
            if 'daily_messages' in user_data:
                user_data['daily_messages'] = [
                    msg_time for msg_time in user_data['daily_messages'] 
                    if msg_time > day_ago
                ]
            
            # User ganz entfernen wenn keine aktuellen Nachrichten
            if (not user_data.get('messages') and 
                not user_data.get('daily_messages')):
                del limits[username]
                cleaned_users += 1
        
        save_rate_limits(limits)
        
        return {
            'initial_users': initial_users,
            'final_users': len(limits),
            'cleaned_users': cleaned_users
        }
        
    except Exception as e:
        logger.error(f"Rate limit cleanup task error: {e}")
        raise

async def system_health_check():
    """System-Gesundheitscheck"""
    try:
        results = {}
        
        # Speicherplatz prüfen
        import shutil
        disk_usage = shutil.disk_usage(".")
        results['disk_free_gb'] = round(disk_usage.free / (1024**3), 2)
        results['disk_used_percent'] = round(
            (disk_usage.used / disk_usage.total) * 100, 1
        )
        
        # Datenbank-Verbindung testen
        try:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute("SELECT 1")
            conn.close()
            results['database_healthy'] = True
        except:
            results['database_healthy'] = False
        
        # Cache-Status
        cache_stats = app_cache.stats()
        results['cache_entries'] = cache_stats['total_entries']
        results['cache_memory_mb'] = cache_stats['memory_usage_mb']
        
        # Performance-Stats falls verfügbar
        if (hasattr(PerformanceMonitoringMiddleware, 'instance') and 
            PerformanceMonitoringMiddleware.instance):
            perf_stats = PerformanceMonitoringMiddleware.instance.get_stats()
            results['avg_response_time'] = perf_stats.get('average_response_time', 0)
            results['total_requests'] = perf_stats.get('total_requests', 0)
        
        return results
        
    except Exception as e:
        logger.error(f"Health check task error: {e}")
        raise

async def user_statistics_update():
    """User-Statistiken aktualisieren"""
    try:
        users = get_all_users()
        
        stats = {
            'total_users': len(users),
            'blocked_users': sum(1 for u in users.values() if u.get('blocked', False)),
            'admin_users': sum(1 for u in users.values() if u.get('is_admin', False)),
        }
        
        # Subscription-Tier-Verteilung
        tier_counts = {'free': 0, 'pro': 0, 'premium': 0}
        for user in users.values():
            tier = user.get('subscription_tier', 'free')
            if tier in tier_counts:
                tier_counts[tier] += 1
        
        stats['subscription_tiers'] = tier_counts
        
        # Chat-Aktivität (falls verfügbar)
        try:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            
            # Nachrichten der letzten 24h
            cursor.execute("""
                SELECT COUNT(*) FROM chat_history 
                WHERE timestamp >= datetime('now', '-1 day')
            """)
            stats['messages_24h'] = cursor.fetchone()[0]
            
            # Aktive User (letzte 7 Tage)
            cursor.execute("""
                SELECT COUNT(DISTINCT username) FROM chat_history 
                WHERE timestamp >= datetime('now', '-7 days')
            """)
            stats['active_users_7d'] = cursor.fetchone()[0]
            
            conn.close()
        except:
            stats['messages_24h'] = 0
            stats['active_users_7d'] = 0
        
        return stats
        
    except Exception as e:
        logger.error(f"User statistics task error: {e}")
        raise

# ──────────────────────────────
# Task-Manager Setup und Registrierung
# ──────────────────────────────

def setup_background_tasks():
    """Registriert alle verfügbaren Background-Tasks"""
    
    # Task-Funktionen registrieren
    task_manager.register_task_function(
        "cache_cleanup", 
        enhanced_cache_cleanup,
        "Bereinigt abgelaufene Cache-Einträge und optimiert Speichernutzung"
    )
    
    task_manager.register_task_function(
        "database_maintenance",
        database_maintenance,
        "Führt VACUUM und ANALYZE aus, löscht alte Chat-Nachrichten"
    )
    
    task_manager.register_task_function(
        "rate_limit_cleanup",
        rate_limit_cleanup,
        "Bereinigt alte Rate-Limit-Daten"
    )
    
    task_manager.register_task_function(
        "system_health_check",
        system_health_check,
        "Überprüft System-Gesundheit (Speicher, DB, Performance)"
    )
    
    task_manager.register_task_function(
        "user_statistics",
        user_statistics_update,
        "Aktualisiert User- und Chat-Statistiken"
    )
    
    # Wiederkehrende Tasks planen
    task_manager.schedule_recurring_task(
        "cache_cleanup_scheduled",
        enhanced_cache_cleanup,
        interval_seconds=300,  # Alle 5 Minuten
        run_immediately=False
    )
    
    task_manager.schedule_recurring_task(
        "rate_limit_cleanup_scheduled", 
        rate_limit_cleanup,
        interval_seconds=3600,  # Stündlich
        run_immediately=False
    )
    
    task_manager.schedule_recurring_task(
        "health_check_scheduled",
        system_health_check, 
        interval_seconds=900,  # Alle 15 Minuten
        run_immediately=True
    )
    
    task_manager.schedule_recurring_task(
        "database_maintenance_scheduled",
        database_maintenance,
        interval_seconds=86400,  # Täglich
        run_immediately=False
    )
    
    logger.info("[TASKS] Background-Task-System initialisiert")

# Alte cache_cleanup_background Funktion ersetzen
async def legacy_cache_cleanup_background():
    """Legacy-Funktion - wird ersetzt durch Task-Manager"""
    # Diese Funktion wird nicht mehr gebraucht
    # Der Task-Manager übernimmt das jetzt
    pass
# Globaler Task-Manager
task_manager = BackgroundTaskManager(max_concurrent_tasks=3)
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
# Enhanced Caching System
# ──────────────────────────────

class SimpleCache:
    """Thread-safe Memory Cache mit TTL"""
    
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

# Globaler Cache
app_cache = SimpleCache(default_ttl=300)

def cache_result(key_prefix: str, ttl: int = 300):
    """Decorator für Funktions-Caching"""
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
    """User-bezogene Cache-Einträge löschen"""
    keys_to_delete = [
        f"user:{username}",
        f"user_persona:{username}",
        f"user_tier:{username}",
        f"available_personas:{username}"
    ]
    
    for key in keys_to_delete:
        app_cache.delete(key)

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

# ──────────────────────────────
# Cached Database Functions  
# ──────────────────────────────

@cache_result("user", ttl=600)
def get_user_cached(username: str):
    """Cached Version von get_user()"""
    return get_user(username)

@cache_result("user_persona", ttl=300)
def get_user_persona_cached(username: str):
    """Cached Version von get_user_persona()"""
    return get_user_persona(username)

@cache_result("user_tier", ttl=600)
def get_user_subscription_tier_cached(username: str):
    """Cached Version von get_user_subscription_tier()"""
    return get_user_subscription_tier(username)

@cache_result("available_personas", ttl=600)
def get_available_personas_for_user_cached(username: str):
    """Cached Version von get_available_personas_for_user()"""
    return get_available_personas_for_user(username)

@cache_result("all_users", ttl=120)
def get_all_users_cached():
    """Cached Version von get_all_users()"""
    return get_all_users()

# ──────────────────────────────
# Erweiterte Funktionen mit Cache-Invalidation
# ──────────────────────────────

def save_user_persona_cached(username: str, persona: str):
    """save_user_persona mit Cache-Invalidierung"""
    save_user_persona(username, persona)
    invalidate_user_cache(username)

def set_user_subscription_tier_cached(username: str, tier: str):
    """set_user_subscription_tier mit Cache-Invalidierung"""
    set_user_subscription_tier(username, tier)
    invalidate_user_cache(username)
    app_cache.delete("all_users:")

def toggle_user_block_cached(username: str):
    """toggle_user_block mit Cache-Invalidierung"""
    toggle_user_block(username)
    invalidate_user_cache(username)
    app_cache.delete("all_users:")

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
    row = cursor.fetchone()
    if not row:
        conn.close()
        return
    current = row[0]
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
    current_persona = get_user_persona_cached(username)
    available_personas = get_available_personas_for_user_cached(username)
    
    if current_persona not in available_personas:
        # Fallback zu Standard-Persona
        current_persona = "standard"
        save_user_persona_cached(username, current_persona)
    
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
    current_persona = get_user_persona_cached(username)
    user_tier = get_user_subscription_tier_cached(username)
    
    # Verfügbare Personas für User-Tier
    available_personas = get_available_personas_for_user_cached(username)
    
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
    available_personas = get_available_personas_for_user_cached(username)
    
    if persona in available_personas:
        save_user_persona_cached(username, persona)
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
    
    if username != "admin":  # Admin kann sich nicht selbst blockieren
        toggle_user_block_cached(username)
        logger.info(f"[ADMIN] User blockiert/freigeschaltet: {username}")
    
    return RedirectResponse("/admin", status_code=302)

@app.get("/admin/cache-stats")
async def cache_stats(request: Request):
    """Cache-Statistiken für Admin"""
    guard = admin_redirect_guard(request)
    if guard:
        return guard
    
    return {
        "cache_stats": app_cache.stats(),
        "cleanup_available": True
    }

@app.post("/admin/clear-cache")
async def clear_cache(request: Request):
    """Cache leeren (Admin)"""
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
    """Admin kann User-Subscription-Tier ändern"""
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
    cache_stats = app_cache.stats()
    
    return templates.TemplateResponse("admin_performance.html", {
        "request": request,
        "stats": stats,
        "cache_stats": cache_stats
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
    
    return PerformanceMonitoringMiddleware.instance.get_stats()

@app.get("/admin/database", response_class=HTMLResponse)
async def admin_database(request: Request):
    guard = admin_redirect_guard(request)
    if guard:
        return guard

    redirect = require_active_session(request)
    if redirect:
        return redirect

    # --- SQLite-Stats einsammeln ---
    stats = {}
    try:
        size_bytes = os.path.getsize(DB_PATH) if os.path.exists(DB_PATH) else 0
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()

        def fetch_one(sql):
            try:
                cur.execute(sql)
                row = cur.fetchone()
                return row[0] if row else None
            except Exception:
                return None

        # PRAGMAs / Infos
        stats["sqlite_version"]   = sqlite3.sqlite_version
        stats["page_count"]       = fetch_one("PRAGMA page_count;")
        stats["page_size"]        = fetch_one("PRAGMA page_size;")
        stats["freelist_count"]   = fetch_one("PRAGMA freelist_count;")
        stats["journal_mode"]     = fetch_one("PRAGMA journal_mode;")
        stats["wal_autocheckpoint"]= fetch_one("PRAGMA wal_autocheckpoint;")
        stats["cache_size"]       = fetch_one("PRAGMA cache_size;")
        stats["synchronous"]      = fetch_one("PRAGMA synchronous;")
        stats["foreign_keys"]     = fetch_one("PRAGMA foreign_keys;")
        stats["locking_mode"]     = fetch_one("PRAGMA locking_mode;")
        stats["db_size_bytes"]    = size_bytes
        stats["db_size_mb"]       = round((size_bytes or 0) / (1024*1024), 2)

        # Tabellen + Zeilenzahlen (optional)
        try:
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
            tables = [r[0] for r in cur.fetchall()]
            table_counts = {}
            for t in tables:
                try:
                    cur.execute(f"SELECT COUNT(*) FROM {t}")
                    table_counts[t] = cur.fetchone()[0]
                except Exception:
                    table_counts[t] = None
            stats["tables"] = table_counts
        except Exception:
            stats["tables"] = {}

        conn.close()
    except Exception as e:
        logger.error(f"/admin/database stats error: {e}")

    ctx = {
        "request": request,
        "db_stats": stats,              # <-- WICHTIG
        "db_path": DB_PATH,
    }
    return templates.TemplateResponse("admin_database.html", ctx)

# ──────────────────────────────
# Admin Routes für Background Task Management  
# ──────────────────────────────

@app.get("/admin/tasks", response_class=HTMLResponse)
async def admin_tasks(request: Request):
    """Admin-Seite für Task-Management"""
    guard = admin_redirect_guard(request)
    if guard:
        return guard
    
    redirect = require_active_session(request)
    if redirect:
        return redirect
    
    # Task-Statistiken sammeln
    task_stats = task_manager.get_stats()
    all_tasks = task_manager.get_all_tasks()
    running_tasks = task_manager.get_running_tasks()
    
    # Letzte Tasks (neueste zuerst)
    recent_tasks = sorted(all_tasks, key=lambda x: x.created_at, reverse=True)[:20]
    
    # Registrierte Task-Funktionen
    registered_functions = task_manager.registered_task_functions
    
    # Geplante Tasks
    scheduled_info = []
    for name, schedule in task_manager.scheduled_tasks.items():
        scheduled_info.append({
            'name': name,
            'interval_minutes': round(schedule['interval'] / 60, 1),
            'next_run': schedule['next_run'],
            'last_run': schedule['last_run'],
            'run_count': schedule['run_count']
        })
    
    return templates.TemplateResponse("admin_tasks.html", {
        "request": request,
        "task_stats": task_stats,
        "recent_tasks": recent_tasks,
        "running_tasks": running_tasks,
        "registered_functions": registered_functions,
        "scheduled_tasks": scheduled_info
    })

@app.post("/admin/tasks/run")
async def run_manual_task(request: Request, task_name: str = Form(...)):
    """Startet einen Task manuell"""
    guard = admin_redirect_guard(request)
    if guard:
        return guard
    
    redirect = require_active_session(request)
    if redirect:
        return redirect
    
    if task_name in task_manager.registered_task_functions:
        func_info = task_manager.registered_task_functions[task_name]
        task_id = await task_manager.run_task(
            f"{task_name} (manual)", 
            func_info['function']
        )
        
        logger.info(f"[ADMIN] Manual task started: {task_name} (ID: {task_id})")
        return RedirectResponse(f"/admin/tasks?started={task_id}", status_code=302)
    
    return RedirectResponse("/admin/tasks?error=unknown_task", status_code=302)

@app.post("/admin/tasks/cancel/{task_id}")
async def cancel_task(request: Request, task_id: str):
    """Bricht einen laufenden Task ab"""
    guard = admin_redirect_guard(request)
    if guard:
        return guard
    
    redirect = require_active_session(request)
    if redirect:
        return redirect
    
    success = task_manager.cancel_task(task_id)
    if success:
        logger.info(f"[ADMIN] Task cancelled: {task_id}")
        return {"success": True, "message": "Task abgebrochen"}
    
    return {"success": False, "message": "Task konnte nicht abgebrochen werden"}

@app.get("/api/admin/tasks/status")
async def api_task_status(request: Request):
    """API für Live-Task-Status (AJAX)"""
    guard = admin_redirect_guard(request)
    if guard:
        return {"error": "Unauthorized"}
    
    redirect = require_active_session(request)
    if redirect:
        return {"error": "Session expired"}
    
    stats = task_manager.get_stats()
    running_tasks = [
        {
            "id": task.id,
            "name": task.name,
            "status": task.status.value,
            "progress": task.progress,
            "started_at": task.started_at.isoformat() if task.started_at else None,
            "duration_seconds": (
                (datetime.now() - task.started_at).total_seconds() 
                if task.started_at else 0
            )
        }
        for task in task_manager.get_running_tasks()
    ]
    
    return {
        "stats": stats,
        "running_tasks": running_tasks,
        "timestamp": datetime.now().isoformat()
    }

@app.get("/api/admin/tasks/{task_id}/details")
async def api_task_details(request: Request, task_id: str):
    """Detailinformationen zu einem Task"""
    guard = admin_redirect_guard(request)
    if guard:
        return {"error": "Unauthorized"}
    
    redirect = require_active_session(request)
    if redirect:
        return {"error": "Session expired"}
    
    task_info = task_manager.get_task_info(task_id)
    if not task_info:
        return {"error": "Task not found"}
    
    return {
        "id": task_info.id,
        "name": task_info.name,
        "status": task_info.status.value,
        "created_at": task_info.created_at.isoformat(),
        "started_at": task_info.started_at.isoformat() if task_info.started_at else None,
        "completed_at": task_info.completed_at.isoformat() if task_info.completed_at else None,
        "progress": task_info.progress,
        "error_message": task_info.error_message,
        "result": task_info.result,
        "metadata": task_info.metadata,
        "duration_seconds": (
            (task_info.completed_at - task_info.started_at).total_seconds()
            if task_info.started_at and task_info.completed_at else None
        )
    }

@app.post("/admin/tasks/cleanup")
async def cleanup_old_tasks(request: Request, older_than_hours: int = Form(24)):
    """Bereinigt alte Task-Einträge"""
    guard = admin_redirect_guard(request)
    if guard:
        return guard
    
    redirect = require_active_session(request)
    if redirect:
        return redirect
    
    cleaned_count = task_manager.cleanup_old_tasks(older_than_hours)
    logger.info(f"[ADMIN] Cleaned {cleaned_count} old task entries")
    
    return RedirectResponse(f"/admin/tasks?cleaned={cleaned_count}", status_code=302)

# ──────────────────────────────
# Task-Manager Integration in Startup
# ──────────────────────────────

# Diese Funktion ersetzt deine bisherige startup-Funktion
async def enhanced_startup():
    """Erweiterte Startup-Funktion mit Background-Task-Manager"""
    init_db()
    
    # Background-Task-System initialisieren
    setup_background_tasks()
    
    # Task-Manager-Cleanup-Task starten (ersetzt cache_cleanup_background)
    task_manager.schedule_recurring_task(
        "task_manager_cleanup",
        lambda: task_manager.cleanup_old_tasks(24),
        interval_seconds=21600,  # Alle 6 Stunden
        run_immediately=False
    )
    
    logger.info("[STARTUP] KI-Chat mit erweiterten Background-Tasks gestartet")

# ──────────────────────────────
# Utility-Funktionen für Tasks
# ──────────────────────────────

def get_task_summary() -> dict:
    """Kurze Task-Zusammenfassung für Dashboard"""
    stats = task_manager.get_stats()
    running = task_manager.get_running_tasks()
    
    # Letzter Fehler
    recent_failed = [
        task for task in task_manager.get_all_tasks() 
        if task.status == TaskStatus.FAILED
    ]
    recent_failed.sort(key=lambda x: x.completed_at or x.created_at, reverse=True)
    last_error = recent_failed[0] if recent_failed else None
    
    return {
        'total_tasks': stats['total_tasks'],
        'running_count': stats['running_tasks'],
        'failed_count': stats['failed_tasks'],
        'running_task_names': [task.name for task in running],
        'last_error': {
            'task_name': last_error.name,
            'error_message': last_error.error_message,
            'when': last_error.completed_at.strftime('%H:%M:%S') if last_error.completed_at else 'Unknown'
        } if last_error else None,
        'scheduler_active': stats['scheduler_active']
    }

# API-Endpoint für Dashboard-Widget
@app.get("/api/admin/tasks/summary")
async def api_task_summary(request: Request):
    """Task-Zusammenfassung für Dashboard-Widget"""
    guard = admin_redirect_guard(request)
    if guard:
        return {"error": "Unauthorized"}
    
    return get_task_summary()

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
# Background Tasks
# ──────────────────────────────
async def cache_cleanup_background():
    """Background-Task für Cache-Bereinigung"""
    await asyncio.sleep(60)  # Warten bis App vollständig gestartet
    
    while True:
        try:
            cleaned = app_cache.cleanup_expired()
            if cleaned > 0:
                logger.info(f"[CACHE] {cleaned} abgelaufene Einträge bereinigt")
            await asyncio.sleep(300)  # Alle 5 Minuten
        except Exception as e:
            logger.error(f"[CACHE ERROR] {e}")
            await asyncio.sleep(300)

# ──────────────────────────────
# Startup
# ──────────────────────────────
@app.on_event("startup")
async def startup():
    init_db()
    setup_background_tasks()  # Background-Task-System initialisieren
    logger.info("[STARTUP] KI-Chat mit erweiterten Background-Tasks gestartet")