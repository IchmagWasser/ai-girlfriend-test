from fastapi import FastAPI, Request, Form, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from ollama_chat import get_response
from auth import check_login, save_user
from dotenv import load_dotenv
import os
import logging
import json

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

# 🏠 Weiterleitung auf /chat – aber nur, wenn eingeloggt
@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    request.session.clear()  # 🧹 Session löschen beim Start
    return RedirectResponse("/login", status_code=status.HTTP_302_FOUND)

# 💬 Chatseite – mit Username anzeigen
@app.get("/chat", response_class=HTMLResponse)
async def chat_page(request: Request):
    if not request.session.get("username"):
        return RedirectResponse("/login", status_code=status.HTTP_302_FOUND)
    return templates.TemplateResponse("chat.html", {
        "request": request,
        "username": request.session["username"]
    })

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

# 🆕 Registrierung verarbeiten mit Frage & Antwort
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

# 🚪 Logout
@app.get("/logout")
async def logout(request: Request):
    username = request.session.get("username")
    request.session.clear()
    logging.info(f"Nutzer '{username}' hat sich ausgeloggt.")
    return RedirectResponse("/login")

# 🤖 Chat-Antwort von der KI
@app.post("/chat")
async def chat(req: Request):
    username = req.session.get("username")
    if not username:
        return {"reply": "Nicht eingeloggt."}

    data = await req.json()
    user_message = data.get("message", "")

    # Verlauf laden, Nachricht hinzufügen, speichern
    chat_history = get_user_history(username)
    chat_history.append({"role": "user", "content": user_message})

    response_text = get_response(user_message)

    chat_history.append({"role": "assistant", "content": response_text})
    save_user_history(username, chat_history)

    return {"reply": response_text}

# 🧹 Optional: Sessions beim Start löschen (nicht nötig bei Cookie-Sessions)
@app.on_event("startup")
def clear_all_sessions():
    pass
# 📋 Admin-Ansicht aller Benutzer (unsicher – nur für Tests!)
@app.get("/admin", response_class=HTMLResponse)
async def show_users(request: Request):
    if not request.session.get("username") == "admin":
        return HTMLResponse("Zugriff verweigert", status_code=403)

    if not os.path.exists("users.json"):
        return HTMLResponse("Keine Benutzer gefunden.")

    with open("users.json", "r", encoding="utf-8") as f:
        try:
            users = json.load(f)
        except json.JSONDecodeError:
            return HTMLResponse("Fehler beim Lesen der Datei.")

    # HTML-Tabelle bauen
    html = "<h1>Registrierte Benutzer</h1><table border='1'><tr><th>Benutzername</th><th>Passwort-Hash</th><th>Frage</th><th>Antwort</th></tr>"
    for user, data in users.items():
        html += f"<tr><td>{user}</td><td>{data.get('password')}</td><td>{data.get('question')}</td><td>{data.get('answer')}</td></tr>"
    html += "</table>"

    return HTMLResponse(html)
# 🔐 Admin – Alle Benutzer anzeigen
@app.get("/admin/users", response_class=HTMLResponse)
async def admin_users(request: Request):
    username = request.session.get("username")
    if username != "admin":
        return RedirectResponse("/login", status_code=302)

    users = {}
    if os.path.exists("users.json"):
        with open("users.json", "r", encoding="utf-8") as f:
            try:
                users = json.load(f)
            except json.JSONDecodeError:
                pass

    return templates.TemplateResponse("admin_users.html", {
        "request": request,
        "users": users,
        "admin": username
    })
