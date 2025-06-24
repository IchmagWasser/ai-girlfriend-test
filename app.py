# app.py
from fastapi import FastAPI, Request, Form, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from dotenv import load_dotenv
import os
import logging
import json

from ollama_chat import get_response
from auth import check_login, save_user, verify_security_answer, get_security_question, reset_password

# 🔐 .env laden
load_dotenv()
SECRET_KEY = os.getenv("SECRET_KEY", "fallback_key_123")

# 📋 Logging aktivieren
logging.basicConfig(level=logging.INFO)

# 🚀 App starten
app = FastAPI()
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY)

# 📁 Statische Dateien
app.mount("/static", StaticFiles(directory="static"), name="static")

# 📄 HTML-Templates
templates = Jinja2Templates(directory="templates")

# 🔁 Verlauf pro Benutzer speichern/laden
def get_user_history(username: str):
    path = f"user_chats/{username}.json"
    if os.path.exists(path):
        with open(path, "r") as f:
            return json.load(f)
    return []

def save_user_history(username: str, history: list):
    os.makedirs("user_chats", exist_ok=True)
    path = f"user_chats/{username}.json"
    with open(path, "w") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)

# 🏠 Startseite leitet auf Login um + löscht Session
@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=status.HTTP_302_FOUND)

# 🔑 Login-Seite
@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request, "error": ""})

# 🔑 Login-Verarbeitung
@app.post("/login")
async def login(request: Request, username: str = Form(...), password: str = Form(...)):
    if check_login(username, password):
        request.session["username"] = username
        logging.info(f"Nutzer '{username}' erfolgreich eingeloggt.")
        return RedirectResponse("/chat", status_code=status.HTTP_302_FOUND)
    return templates.TemplateResponse("login.html", {"request": request, "error": "Login fehlgeschlagen"})

# 🆕 Registrierung anzeigen
@app.get("/register", response_class=HTMLResponse)
async def register_page(request: Request):
    return templates.TemplateResponse("register.html", {"request": request, "error": ""})

# 🆕 Registrierung verarbeiten
@app.post("/register")
async def register(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    question: str = Form(...),
    answer: str = Form(...)
):
    success = save_user(username, password, question, answer)
    if success:
        logging.info(f"Nutzer '{username}' registriert.")
        return RedirectResponse("/login", status_code=status.HTTP_302_FOUND)
    return templates.TemplateResponse("register.html", {"request": request, "error": "Nutzername existiert bereits."})

# 🔐 Passwort vergessen
@app.get("/reset", response_class=HTMLResponse)
async def reset_page(request: Request):
    return templates.TemplateResponse("reset.html", {"request": request, "error": "", "success": ""})

@app.post("/reset", response_class=HTMLResponse)
async def reset_password_route(
    request: Request,
    username: str = Form(...),
    answer: str = Form(...),
    new_password: str = Form(...)
):
    if not verify_security_answer(username, answer):
        return templates.TemplateResponse("reset.html", {"request": request, "error": "Antwort falsch", "success": ""})

    if reset_password(username, new_password):
        return templates.TemplateResponse("reset.html", {"request": request, "error": "", "success": "Passwort erfolgreich geändert"})

    return templates.TemplateResponse("reset.html", {"request": request, "error": "Fehler beim Zurücksetzen", "success": ""})

# 💬 Chatseite
@app.get("/chat", response_class=HTMLResponse)
async def chat_page(request: Request):
    if not request.session.get("username"):
        return RedirectResponse("/login", status_code=status.HTTP_302_FOUND)
    return templates.TemplateResponse("chat.html", {
        "request": request,
        "username": request.session["username"]
    })

# 🤖 Chat-Antwort von der KI
@app.post("/chat")
async def chat(req: Request):
    username = req.session.get("username")
    if not username:
        return {"reply": "Nicht eingeloggt."}

    data = await req.json()
    user_message = data.get("message", "")

    chat_history = get_user_history(username)
    chat_history.append({"role": "user", "content": user_message})

    response_text = get_response(user_message)
    chat_history.append({"role": "assistant", "content": response_text})

    save_user_history(username, chat_history)
    return {"reply": response_text}

# 🚪 Logout
@app.get("/logout")
async def logout(request: Request):
    username = request.session.get("username")
    request.session.clear()
    logging.info(f"Nutzer '{username}' hat sich ausgeloggt.")
    return RedirectResponse("/login")

# 👑 Admin-Bereich
@app.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request):
    username = request.session.get("username")
    if username != "admin":
        return HTMLResponse("Zugriff verweigert", status_code=403)

    users = {}
    if os.path.exists("users.json"):
        with open("users.json", "r", encoding="utf-8") as f:
            try:
                users = json.load(f)
            except json.JSONDecodeError:
                return HTMLResponse("Fehler beim Lesen von users.json.")

    html = "<h1>Adminbereich – Alle registrierten Benutzer</h1><table border='1'>"
    html += "<tr><th>Benutzername</th><th>Passwort-Hash</th><th>Frage</th><th>Antwort</th></tr>"
    for user, data in users.items():
        html += f"<tr><td>{user}</td><td>{data.get('password')}</td><td>{data.get('question')}</td><td>{data.get('answer')}</td></tr>"
    html += "</table>"
    return HTMLResponse(html)

# 🧹 Startup (optional)
@app.on_event("startup")
def clear_all_sessions():
    pass
