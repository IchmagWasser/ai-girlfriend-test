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

# 🏠 Weiterleitung auf /chat – aber nur, wenn eingeloggt
@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    if not request.session.get("username"):
        return RedirectResponse("/login", status_code=status.HTTP_302_FOUND)
    return RedirectResponse("/chat", status_code=status.HTTP_302_FOUND)

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

# 🆕 Registrierung verarbeiten
@app.post("/register")
async def register(request: Request, username: str = Form(...), password: str = Form(...)):
    success = save_user(username, password)
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
    if not req.session.get("username"):
        return {"reply": "Nicht eingeloggt."}

    data = await req.json()
    user_message = data.get("message", "")

    chat_history = req.session.get("chat_history", [])
    chat_history.append({"role": "user", "content": user_message})

    response_text = get_response(user_message)

    chat_history.append({"role": "assistant", "content": response_text})
    req.session["chat_history"] = chat_history

    return {"reply": response_text}

# 🧹 Optional: Sessions beim Start löschen (bei Datenbank oder Redis)
@app.on_event("startup")
def clear_all_sessions():
    # ⚠️ Nicht nötig bei Cookie-basierten Sessions
    pass
