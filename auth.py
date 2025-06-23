# auth.py
import hashlib
import json
import os

USER_DATA_FILE = "users.json"

# Passwort & Antwort hashen
def hash_value(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()

# Benutzer speichern inkl. Sicherheitsfrage
def save_user(username: str, password: str, question: str, answer: str) -> bool:
    password_hash = hash_value(password)
    answer_hash = hash_value(answer)

    if os.path.exists(USER_DATA_FILE):
        with open(USER_DATA_FILE, "r") as f:
            try:
                users = json.load(f)
            except json.JSONDecodeError:
                users = {}
    else:
        users = {}

    if username in users:
        return False  # Benutzer existiert schon

    users[username] = {
        "password": password_hash,
        "question": question,
        "answer": answer_hash
    }

    with open(USER_DATA_FILE, "w") as f:
        json.dump(users, f, ensure_ascii=False, indent=2)

    return True

# Login prüfen
def check_login(username: str, password: str) -> bool:
    password_hash = hash_value(password)

    if not os.path.exists(USER_DATA_FILE):
        return False

    with open(USER_DATA_FILE, "r") as f:
        try:
            users = json.load(f)
        except json.JSONDecodeError:
            return False

    return (
        username in users and
        users[username]["password"] == password_hash
    )

# Sicherheitsfrage abrufen
def get_security_question(username: str) -> str:
    if not os.path.exists(USER_DATA_FILE):
        return ""

    with open(USER_DATA_FILE, "r") as f:
        try:
            users = json.load(f)
        except json.JSONDecodeError:
            return ""

    return users.get(username, {}).get("question", "")

# Sicherheitsantwort prüfen
def check_security_answer(username: str, answer: str) -> bool:
    answer_hash = hash_value(answer)

    if not os.path.exists(USER_DATA_FILE):
        return False

    with open(USER_DATA_FILE, "r") as f:
        try:
            users = json.load(f)
        except json.JSONDecodeError:
            return False

    return (
        username in users and
        users[username]["answer"] == answer_hash
    )
