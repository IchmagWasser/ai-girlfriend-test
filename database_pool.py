# database_pool.py - SQLite Connection Pool Implementation

import sqlite3
import threading
import time
import queue
import logging
from contextlib import contextmanager
from typing import Optional, Dict, Any, List
from dataclasses import dataclass
from functools import wraps

logger = logging.getLogger("database_pool")

@dataclass
class ConnectionInfo:
    """Information über eine Connection"""
    connection: sqlite3.Connection
    created_at: float
    last_used: float
    query_count: int
    in_use: bool

class SQLiteConnectionPool:
    """
    Thread-safe SQLite Connection Pool mit:
    - Automatisches Connection Management
    - Health Checks
    - Query Logging
    - Performance Monitoring
    """
    
    def __init__(
        self, 
        database_path: str, 
        min_connections: int = 2,
        max_connections: int = 10,
        max_age_seconds: int = 3600,  # 1 Stunde
        timeout: float = 30.0
    ):
        self.database_path = database_path
        self.min_connections = min_connections
        self.max_connections = max_connections
        self.max_age_seconds = max_age_seconds
        self.timeout = timeout
        
        # Pool Management
        self._pool = queue.Queue(maxsize=max_connections)
        self._all_connections: Dict[int, ConnectionInfo] = {}
        self._lock = threading.RLock()
        
        # Statistics
        self.stats = {
            'total_connections_created': 0,
            'total_queries_executed': 0,
            'total_query_time': 0.0,
            'active_connections': 0,
            'pool_hits': 0,
            'pool_misses': 0
        }
        
        # Background cleanup
        self._cleanup_thread = None
        self._stop_cleanup = threading.Event()
        
        # Initialisierung
        self._initialize_pool()
        self._start_cleanup_thread()
    
    def _create_connection(self) -> sqlite3.Connection:
        """Erstellt eine neue SQLite Connection mit optimalen Einstellungen"""
        conn = sqlite3.connect(
            self.database_path,
            timeout=self.timeout,
            check_same_thread=False
        )
        
        # Optimale SQLite Settings für Performance
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA cache_size=10000")
        conn.execute("PRAGMA temp_store=memory")
        conn.execute("PRAGMA mmap_size=268435456")  # 256MB
        
        # Row Factory für bessere Handhabung
        conn.row_factory = sqlite3.Row
        
        conn_id = id(conn)
        current_time = time.time()
        
        conn_info = ConnectionInfo(
            connection=conn,
            created_at=current_time,
            last_used=current_time,
            query_count=0,
            in_use=False
        )
        
        with self._lock:
            self._all_connections[conn_id] = conn_info
            self.stats['total_connections_created'] += 1
            self.stats['active_connections'] += 1
        
        logger.debug(f"[POOL] Neue Connection erstellt: {conn_id}")
        return conn
    
    def _initialize_pool(self):
        """Initialisiert den Pool mit Mindest-Connections"""
        for _ in range(self.min_connections):
            try:
                conn = self._create_connection()
                self._pool.put(conn, block=False)
            except Exception as e:
                logger.error(f"[POOL] Fehler beim Initialisieren: {e}")
    
    def _start_cleanup_thread(self):
        """Startet Background-Thread für Cleanup"""
        def cleanup_worker():
            while not self._stop_cleanup.wait(60):  # Alle 60 Sekunden
                self._cleanup_old_connections()
        
        self._cleanup_thread = threading.Thread(target=cleanup_worker, daemon=True)
        self._cleanup_thread.start()
    
    def _cleanup_old_connections(self):
        """Entfernt alte, ungenutzte Connections"""
        current_time = time.time()
        connections_to_close = []
        
        with self._lock:
            for conn_id, conn_info in list(self._all_connections.items()):
                if (not conn_info.in_use and 
                    current_time - conn_info.last_used > self.max_age_seconds and
                    len(self._all_connections) > self.min_connections):
                    connections_to_close.append(conn_id)
        
        for conn_id in connections_to_close:
            try:
                conn_info = self._all_connections.pop(conn_id)
                conn_info.connection.close()
                self.stats['active_connections'] -= 1
                logger.debug(f"[POOL] Connection {conn_id} wegen Alter geschlossen")
            except Exception as e:
                logger.error(f"[POOL] Fehler beim Schließen von Connection {conn_id}: {e}")
    
    @contextmanager
    def get_connection(self):
        """
        Context Manager für Connection-Verwendung
        
        Usage:
            with db_pool.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM users")
                result = cursor.fetchall()
        """
        conn = None
        conn_id = None
        start_time = time.time()
        
        try:
            # Connection aus Pool holen
            try:
                conn = self._pool.get(timeout=5.0)
                conn_id = id(conn)
                self.stats['pool_hits'] += 1
                logger.debug(f"[POOL] Connection {conn_id} aus Pool geholt")
            except queue.Empty:
                # Neue Connection erstellen wenn Pool leer
                if len(self._all_connections) < self.max_connections:
                    conn = self._create_connection()
                    conn_id = id(conn)
                    self.stats['pool_misses'] += 1
                    logger.debug(f"[POOL] Neue Connection {conn_id} erstellt (Pool leer)")
                else:
                    raise Exception("Connection Pool ausgelastet")
            
            # Connection als verwendet markieren
            if conn_id in self._all_connections:
                with self._lock:
                    self._all_connections[conn_id].in_use = True
                    self._all_connections[conn_id].last_used = time.time()
            
            yield conn
            
        finally:
            # Connection zurückgeben
            if conn and conn_id:
                try:
                    # Query-Stats aktualisieren
                    query_time = time.time() - start_time
                    if conn_id in self._all_connections:
                        with self._lock:
                            conn_info = self._all_connections[conn_id]
                            conn_info.in_use = False
                            conn_info.query_count += 1
                            self.stats['total_queries_executed'] += 1
                            self.stats['total_query_time'] += query_time
                    
                    # Connection zurück in Pool
                    self._pool.put(conn, block=False)
                    logger.debug(f"[POOL] Connection {conn_id} zurückgegeben")
                    
                except queue.Full:
                    # Pool voll - Connection schließen
                    conn.close()
                    if conn_id in self._all_connections:
                        with self._lock:
                            del self._all_connections[conn_id]
                            self.stats['active_connections'] -= 1
                    logger.debug(f"[POOL] Connection {conn_id} geschlossen (Pool voll)")
                except Exception as e:
                    logger.error(f"[POOL] Fehler beim Zurückgeben von Connection {conn_id}: {e}")
    
    def execute_query(self, query: str, params: tuple = None, fetch_one: bool = False, fetch_all: bool = True):
        """
        Führt eine Query mit automatischem Connection Management aus
        
        Args:
            query: SQL Query
            params: Parameter für die Query
            fetch_one: Nur ein Ergebnis zurückgeben
            fetch_all: Alle Ergebnisse zurückgeben
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            if params:
                cursor.execute(query, params)
            else:
                cursor.execute(query)
            
            if query.strip().upper().startswith(('INSERT', 'UPDATE', 'DELETE')):
                conn.commit()
                return cursor.rowcount
            elif fetch_one:
                return cursor.fetchone()
            elif fetch_all:
                return cursor.fetchall()
            else:
                return cursor
    
    def get_pool_stats(self) -> Dict[str, Any]:
        """Gibt detaillierte Pool-Statistiken zurück"""
        with self._lock:
            active_conns = sum(1 for info in self._all_connections.values() if info.in_use)
            avg_query_time = (self.stats['total_query_time'] / self.stats['total_queries_executed'] 
                            if self.stats['total_queries_executed'] > 0 else 0)
            
            return {
                'total_connections': len(self._all_connections),
                'active_connections': active_conns,
                'available_connections': len(self._all_connections) - active_conns,
                'pool_size': self._pool.qsize(),
                'max_connections': self.max_connections,
                'total_connections_created': self.stats['total_connections_created'],
                'total_queries_executed': self.stats['total_queries_executed'],
                'average_query_time_ms': round(avg_query_time * 1000, 2),
                'pool_hit_rate': (self.stats['pool_hits'] / 
                                (self.stats['pool_hits'] + self.stats['pool_misses']) * 100
                                if (self.stats['pool_hits'] + self.stats['pool_misses']) > 0 else 0),
                'connection_details': [
                    {
                        'id': conn_id,
                        'created_at': info.created_at,
                        'last_used': info.last_used,
                        'query_count': info.query_count,
                        'in_use': info.in_use,
                        'age_minutes': round((time.time() - info.created_at) / 60, 1)
                    }
                    for conn_id, info in self._all_connections.items()
                ]
            }
    
    def close_all(self):
        """Schließt alle Connections und stoppt den Pool"""
        self._stop_cleanup.set()
        if self._cleanup_thread:
            self._cleanup_thread.join()
        
        # Alle Connections aus Pool holen und schließen
        while not self._pool.empty():
            try:
                conn = self._pool.get_nowait()
                conn.close()
            except queue.Empty:
                break
            except Exception as e:
                logger.error(f"[POOL] Fehler beim Schließen: {e}")
        
        # Verbleibende Connections schließen
        with self._lock:
            for conn_info in self._all_connections.values():
                try:
                    conn_info.connection.close()
                except Exception as e:
                    logger.error(f"[POOL] Fehler beim Schließen: {e}")
            self._all_connections.clear()
            self.stats['active_connections'] = 0
        
        logger.info("[POOL] Alle Connections geschlossen")

# ──────────────────────────────
# Decorator für automatisches Connection Pooling
# ──────────────────────────────

def with_db_connection(pool: SQLiteConnectionPool):
    """
    Decorator für Funktionen die eine DB-Connection brauchen
    
    Usage:
        @with_db_connection(db_pool)
        def get_user(conn, username: str):
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM users WHERE username = ?", (username,))
            return cursor.fetchone()
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            with pool.get_connection() as conn:
                return func(conn, *args, **kwargs)
        return wrapper
    return decorator

# ──────────────────────────────
# Globaler Pool (Singleton Pattern)
# ──────────────────────────────

class DatabaseManager:
    """Singleton Database Manager mit Connection Pool"""
    
    _instance = None
    _pool = None
    
    def __new__(cls, database_path: str = "users.db"):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._pool = SQLiteConnectionPool(
                database_path=database_path,
                min_connections=3,
                max_connections=15,
                max_age_seconds=1800  # 30 Minuten
            )
            logger.info(f"[DB] DatabaseManager initialisiert mit {database_path}")
        return cls._instance
    
    @property
    def pool(self) -> SQLiteConnectionPool:
        return self._pool
    
    def get_stats(self):
        return self._pool.get_pool_stats()
    
    def close(self):
        if self._pool:
            self._pool.close_all()
            DatabaseManager._instance = None
            DatabaseManager._pool = None

# ──────────────────────────────
# Beispiel-Integration in bestehende Funktionen
# ──────────────────────────────

# Globaler Database Manager
db_manager = DatabaseManager()

@with_db_connection(db_manager.pool)
def get_user_pooled(conn, username: str) -> dict:
    """Verbesserte get_user Funktion mit Connection Pool"""
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE username = ?", (username,))
    row = cursor.fetchone()
    
    if row:
        return {
            "username": row["username"],
            "password": row["password"],
            "question": row["question"],
            "answer": row["answer"],
            "is_admin": bool(row["is_admin"]),
            "is_blocked": bool(row["is_blocked"]),
            "persona": row["persona"] if "persona" in row.keys() else "standard",
            "subscription_tier": row["subscription_tier"] if "subscription_tier" in row.keys() else "free"
        }
    return None

@with_db_connection(db_manager.pool)
def get_all_users_pooled(conn) -> dict:
    """Verbesserte get_all_users Funktion mit Connection Pool"""
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users")
    rows = cursor.fetchall()
    
    users = {}
    for row in rows:
        users[row["username"]] = {
            "password": row["password"],
            "question": row["question"],
            "answer": row["answer"],
            "is_admin": bool(row["is_admin"]),
            "blocked": bool(row["is_blocked"]),
            "subscription_tier": row["subscription_tier"] if "subscription_tier" in row.keys() else "free"
        }
    return users

@with_db_connection(db_manager.pool)
def save_user_pooled(conn, username: str, password: str, question: str, answer: str) -> bool:
    """Verbesserte save_user Funktion mit Connection Pool"""
    # Prüfen ob User bereits existiert
    cursor = conn.cursor()
    cursor.execute("SELECT username FROM users WHERE username = ?", (username,))
    if cursor.fetchone():
        return False
    
    try:
        # Passwort hashen (hash_password Funktion muss verfügbar sein)
        import hashlib
        password_hash = hashlib.sha256(password.encode()).hexdigest()
        
        cursor.execute("""
            INSERT INTO users (username, password, question, answer, subscription_tier) 
            VALUES (?, ?, ?, ?, 'free')
        """, (username, password_hash, question, answer))
        
        conn.commit()
        logger.info(f"[DB] Neuer User erstellt: {username}")
        return True
    except Exception as e:
        logger.error(f"[DB] Fehler beim Erstellen von User {username}: {e}")
        conn.rollback()
        return False

# ──────────────────────────────
# Performance Monitoring Integration
# ──────────────────────────────

def get_database_performance_stats():
    """Gibt Performance-Stats für Admin-Panel zurück"""
    return {
        'connection_pool': db_manager.get_stats(),
        'database_path': db_manager.pool.database_path,
        'pool_type': 'SQLite Connection Pool'
    }

# ──────────────────────────────
# Beispiel für Batch Operations
# ──────────────────────────────

@with_db_connection(db_manager.pool)
def batch_insert_chat_history(conn, chat_entries: List[Dict[str, Any]]) -> int:
    """
    Effizientes Batch-Insert für Chat-Historie
    
    Args:
        chat_entries: Liste von {"username": str, "role": str, "content": str}
    """
    cursor = conn.cursor()
    
    cursor.executemany("""
        INSERT INTO chat_history (username, role, content) 
        VALUES (?, ?, ?)
    """, [(entry["username"], entry["role"], entry["content"]) 
          for entry in chat_entries])
    
    conn.commit()
    return cursor.rowcount

@with_db_connection(db_manager.pool)
def get_user_chat_stats(conn, username: str) -> Dict[str, Any]:
    """Erweiterte Chat-Statistiken für einen User"""
    cursor = conn.cursor()
    
    # Gesamtanzahl Nachrichten
    cursor.execute("""
        SELECT COUNT(*) as total_messages,
               SUM(CASE WHEN role = 'user' THEN 1 ELSE 0 END) as user_messages,
               SUM(CASE WHEN role = 'assistant' THEN 1 ELSE 0 END) as assistant_messages,
               MIN(timestamp) as first_message,
               MAX(timestamp) as last_message
        FROM chat_history 
        WHERE username = ?
    """, (username,))
    
    stats = cursor.fetchone()
    
    # Nachrichten pro Tag (letzte 30 Tage)
    cursor.execute("""
        SELECT DATE(timestamp) as date, COUNT(*) as count 
        FROM chat_history 
        WHERE username = ? AND timestamp >= datetime('now', '-30 days')
        GROUP BY DATE(timestamp)
        ORDER BY date DESC
    """, (username,))
    
    daily_stats = cursor.fetchall()
    
    return {
        'total_messages': stats['total_messages'] or 0,
        'user_messages': stats['user_messages'] or 0,
        'assistant_messages': stats['assistant_messages'] or 0,
        'first_message': stats['first_message'],
        'last_message': stats['last_message'],
        'daily_activity': [dict(row) for row in daily_stats]
    }