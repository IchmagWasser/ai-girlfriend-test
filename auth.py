import hashlib
import json
import os

USER_DATA_FILE = "users.json"

# 🔐 Passwort hashen mit SHA256
def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()

# 💾 Benutzer speichern (inkl. Sicherheitsfrage & Antwort)
def save_user(username, password, question, answer):
    password_hash = hash_password(password)

    # Bestehende Benutzerdaten laden
    if os.path.exists(USER_DATA_FILE):
        with open(USER_DATA_FILE, "r", encoding="utf-8") as f:
            try:
                users = json.load(f)
            except json.JSONDecodeError:
                users = {}
    else:
        users = {}

    if username in users:
        return False  # Benutzer existiert bereits

    users[username] = {
        "password": password_hash,
        "question": question,
        "answer": answer
    }

    with open(USER_DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(users, f, ensure_ascii=False, indent=2)

    return True

# 🔑 Login prüfen
def check_login(username, password):
    if not os.path.exists(USER_DATA_FILE):
        return False

    with open(USER_DATA_FILE, "r", encoding="utf-8") as f:
        try:
            users = json.load(f)
        except json.JSONDecodeError:
            return False

    if username not in users:
        return False

    password_hash = hash_password(password)
    return users[username]["password"] == password_hash

# ❓ Sicherheitsfrage abrufen
def get_security_question(username):
    if not os.path.exists(USER_DATA_FILE):
        return None

    with open(USER_DATA_FILE, "r", encoding="utf-8") as f:
        try:
            users = json.load(f)
        except json.JSONDecodeError:
            return None

    return users.get(username, {}).get("question")

# ✅ Antwort auf Sicherheitsfrage prüfen
def verify_security_answer(username, answer) -> bool:
    if not os.path.exists(USER_DATA_FILE):
        return False

    with open(USER_DATA_FILE, "r", encoding="utf-8") as f:
        try:
            users = json.load(f)
        except json.JSONDecodeError:
            return False

    if username not in users:
        return False

    return users[username]["answer"].strip().lower() == answer.strip().lower()
# Passwort zurücksetzen
def reset_password(username, new_password):
    if not os.path.exists(USER_DATA_FILE):
        return False

    with open(USER_DATA_FILE, "r") as f:
        try:
            users = json.load(f)
        except json.JSONDecodeError:
            return False

    if username not in users:
        return False

    users[username]["password"] = hash_password(new_password)

    with open(USER_DATA_FILE, "w") as f:
        json.dump(users, f, ensure_ascii=False, indent=2)

    return True
