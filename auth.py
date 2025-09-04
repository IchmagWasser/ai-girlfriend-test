import sqlite3
import hashlib
import os

DB_PATH = "users.db"

# 🗄️ Datenbank initialisieren (nur beim ersten Start notwendig)
def init_db():
    if not os.path.exists(DB_PATH):
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                username TEXT PRIMARY KEY,
                password TEXT NOT NULL,
                question TEXT NOT NULL,
                answer TEXT NOT NULL
            )
        """)
        conn.commit()
        conn.close()

# 🔐 Passwort-Hashing mit SHA256
def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()

# 🆕 Benutzer registrieren
def save_user(username: str, password: str, question: str, answer: str) -> bool:
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE username = ?", (username,))
        if cursor.fetchone():
            return False  # Benutzer existiert bereits

        hashed_pw = hash_password(password)
        cursor.execute(
            "INSERT INTO users (username, password, question, answer) VALUES (?, ?, ?, ?)",
            (username, hashed_pw, question, answer)
        )
        conn.commit()
        return True
    except Exception as e:
        print("Fehler bei save_user:", e)
        return False
    finally:
        conn.close()

# 🔐 Login überprüfen
def check_login(username: str, password: str) -> bool:
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT password FROM users WHERE username = ?", (username,))
        row = cursor.fetchone()
        if row:
            return row[0] == hash_password(password)
        return False
    finally:
        conn.close()

# 🔄 Passwort zurücksetzen
def reset_password(username: str, new_password: str):
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        hashed_pw = hash_password(new_password)
        cursor.execute(
            "UPDATE users SET password = ? WHERE username = ?",
            (hashed_pw, username)
        )
        conn.commit()
    finally:
        conn.close()

# ❓ Sicherheitsfrage holen
def get_security_question(username: str) -> str:
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT question FROM users WHERE username = ?", (username,))
        row = cursor.fetchone()
        return row[0] if row else ""
    finally:
        conn.close()

# ✅ Sicherheitsantwort überprüfen
def verify_security_answer(username: str, answer: str) -> bool:
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT answer FROM users WHERE username = ?", (username,))
        row = cursor.fetchone()
        return row[0] == answer if row else False
    finally:
        conn.close()
