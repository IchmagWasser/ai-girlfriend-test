import sqlite3
import os

DB_PATH = "users.db"

def init_db():
    if not os.path.exists(DB_PATH):
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("""
            CREATE TABLE IF NOT EXISTS users (
                username TEXT PRIMARY KEY,
                password TEXT NOT NULL,
                question TEXT NOT NULL,
                answer TEXT NOT NULL,
                blocked INTEGER DEFAULT 0
            )
        """)
        conn.commit()
        conn.close()

def get_user(username):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT * FROM users WHERE username = ?", (username,))
    row = c.fetchone()
    conn.close()
    if row:
        return {
            "username": row[0],
            "password": row[1],
            "question": row[2],
            "answer": row[3],
            "blocked": bool(row[4])
        }
    return None

def get_all_users():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT * FROM users")
    rows = c.fetchall()
    conn.close()
    return {
        row[0]: {
            "password": row[1],
            "question": row[2],
            "answer": row[3],
            "blocked": bool(row[4])
        } for row in rows
    }

def save_user(username, password, question, answer):
    if get_user(username):
        return False
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT INTO users (username, password, question, answer) VALUES (?, ?, ?, ?)",
              (username, password, question, answer))
    conn.commit()
    conn.close()
    return True

def delete_user(username):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("DELETE FROM users WHERE username = ?", (username,))
    conn.commit()
    conn.close()

def update_password(username, new_password):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("UPDATE users SET password = ? WHERE username = ?", (new_password, username))
    conn.commit()
    conn.close()

def block_user(username):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("UPDATE users SET blocked = 1 WHERE username = ?", (username,))
    conn.commit()
    conn.close()

def unblock_user(username):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("UPDATE users SET blocked = 0 WHERE username = ?", (username,))
    conn.commit()
    conn.close()
