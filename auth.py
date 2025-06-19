# auth.py
from fastapi import Request, Form, Depends
from fastapi.responses import RedirectResponse
from starlette.status import HTTP_302_FOUND
import hashlib
import json
import os

USER_DATA_FILE = "users.json"

# Hilfsfunktion: Benutzer speichern
def save_user(username, password_hash):
    if os.path.exists(USER_DATA_FILE):
        with open(USER_DATA_FILE, "r") as f:
            users = json.load(f)
    else:
        users = {}

    if username in users:
        return False  # Benutzer existiert bereits

    users[username] = password_hash
    with open(USER_DATA_FILE, "w") as f:
        json.dump(users, f)
    return True

# Hilfsfunktion: Login prüfen
def check_login(username, password_hash):
    if not os.path.exists(USER_DATA_FILE):
        return False

    with open(USER_DATA_FILE, "r") as f:
        users = json.load(f)

    return users.get(username) == password_hash
