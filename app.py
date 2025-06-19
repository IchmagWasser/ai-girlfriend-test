from fastapi import FastAPI, Request, Form, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from ollama_chat import get_response
from auth import check_login, save_user
import os

app = FastAPI()
app.add_middleware(SessionMiddleware, secret_key="supergeheim123456789")

# Static-Files-Ordner für CSS/JS
app.mount("/static", StaticFiles(directory="static"), name="static")

# Template-Ordner für HTML
templates = Jinja2Templates(directory="templates")

# Startseite (Chat nur, wenn eingeloggt)
@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    if not request.session.get("username"):
        return RedirectResponse("/login")
    return templates.TemplateResponse("chat.html", {"request": request})

# Login-Seite anzeigen
@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request, "error": ""})

# Login-Daten verarbeiten
@app.post("/login")
async def login(request: Request, username: str = Form(...), password: str = Form(...)):
    if check_login(username, password):
        request.session["username"] = username
        return RedirectResponse("/", status_code=status.HTTP_302_FOUND)
    return templates.TemplateResponse("login.html", {"request": request, "error": "Login fehlgeschlagen"})

# Registrierungsseite anzeigen
@app.get("/register", response_class=HTMLResponse)
async def register_page(request: Request):
    return templates.TemplateResponse("register.html", {"request": request, "error": ""})

# Registrierung verarbeiten
@app.post("/register")
async def register(request: Request, username: str = Form(...), password: str = Form(...)):
    success = save_user(username, password)
    if success:
        return RedirectResponse("/login", status_code=status.HTTP_302_FOUND)
    return templates.TemplateResponse("register.html", {"request": request, "error": "Nutzername existiert bereits."})

# Chat-Funktion
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