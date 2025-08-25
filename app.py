from fastapi import FastAPI, Request, Form, status
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from dotenv import load_dotenv
import os
import logging
import json
import csv
import io

from ollama_chat import get_response
from auth import (
    check_login,
    save_user,
    verify_security_answer,
    get_security_question,
    reset_password,
    hash_password
)

load_dotenv()
SECRET_KEY = os.getenv("SECRET_KEY", "supersecret")

logging.basicConfig(level=logging.INFO)
app = FastAPI()
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY)

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

USER_DATA_FILE = "users.json"
CHAT_DIR = "user_chats"
os.makedirs(CHAT_DIR, exist_ok=True)

def get_user_history(username: str):
    path = f"{CHAT_DIR}/{username}.json"
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return []

def save_user_history(username: str, history: list):
    with open(f"{CHAT_DIR}/{username}.json", "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)

@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    request.session.clear()
    return templates.TemplateResponse("login.html", {"request": request})

@app.post("/login", response_class=HTMLResponse)
async def login(request: Request, username: str = Form(...), password: str = Form(...)):
    if check_login(username, password):
        with open(USER_DATA_FILE, "r", encoding="utf-8") as f:
            users = json.load(f)

        if users.get(username, {}).get("blocked", False):
            return templates.TemplateResponse("login.html", {
                "request": request,
                "error": "Du wurdest vom Admin gesperrt – melde dich per Mail oder sowas."
            })

        request.session["username"] = username
        return RedirectResponse("/admin" if username == "admin" else "/chat", status_code=302)

    return templates.TemplateResponse("login.html", {"request": request, "error": "Falsche Anmeldedaten"})

@app.get("/register", response_class=HTMLResponse)
async def register_page(request: Request):
    return templates.TemplateResponse("register.html", {"request": request})

@app.post("/register", response_class=HTMLResponse)
async def register(request: Request, username: str = Form(...), password: str = Form(...), question: str = Form(...), answer: str = Form(...)):
    if save_user(username, password, question, answer):
        return RedirectResponse("/", status_code=302)
    return templates.TemplateResponse("register.html", {"request": request, "error": "Benutzer existiert bereits"})

@app.get("/reset", response_class=HTMLResponse)
async def reset_page(request: Request):
    return templates.TemplateResponse("reset.html", {"request": request, "error": "", "success": ""})

@app.post("/reset", response_class=HTMLResponse)
async def reset_post(request: Request, username: str = Form(...), answer: str = Form(...), new_password: str = Form(...)):
    if verify_security_answer(username, answer):
        reset_password(username, new_password)
        return RedirectResponse("/", status_code=302)
    return templates.TemplateResponse("reset.html", {"request": request, "error": "Antwort falsch", "success": ""})

@app.get("/chat", response_class=HTMLResponse)
async def chat_page(request: Request):
    username = request.session.get("username")
    if not username:
        return RedirectResponse("/", status_code=302)
    return templates.TemplateResponse("chat.html", {"request": request, "username": username})

@app.post("/chat")
async def chat(req: Request):
    username = req.session.get("username")
    if not username:
        return {"reply": "❌ Nicht eingeloggt."}

    data = await req.json()
    user_message = data.get("message", "")

    chat_history = get_user_history(username)
    chat_history.append({"role": "user", "content": user_message})

    response_text = get_response(user_message)
    chat_history.append({"role": "assistant", "content": response_text})

    save_user_history(username, chat_history)
    return {"reply": response_text}

@app.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/", status_code=302)

@app.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request):
    username = request.session.get("username")
    if username != "admin":
        return RedirectResponse("/", status_code=302)

    with open(USER_DATA_FILE, "r", encoding="utf-8") as f:
        users = json.load(f)

    return templates.TemplateResponse("admin_users.html", {"request": request, "users": users})

@app.post("/admin/delete-user")
async def delete_user(request: Request, username: str = Form(...)):
    if username == "admin":
        return RedirectResponse("/admin", status_code=302)

    with open(USER_DATA_FILE, "r", encoding="utf-8") as f:
        users = json.load(f)

    if username in users:
        users.pop(username)
        with open(USER_DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(users, f, ensure_ascii=False, indent=2)

    chat_path = f"{CHAT_DIR}/{username}.json"
    if os.path.exists(chat_path):
        os.remove(chat_path)

    return RedirectResponse("/admin", status_code=302)

@app.post("/admin/block-user")
async def block_user(request: Request, username: str = Form(...)):
    with open(USER_DATA_FILE, "r+", encoding="utf-8") as f:
        users = json.load(f)
        if username in users:
            users[username]["blocked"] = True
            f.seek(0)
            json.dump(users, f, ensure_ascii=False, indent=2)
            f.truncate()
    return RedirectResponse("/admin", status_code=302)

@app.post("/admin/unblock-user")
async def unblock_user(request: Request, username: str = Form(...)):
    with open(USER_DATA_FILE, "r+", encoding="utf-8") as f:
        users = json.load(f)
        if username in users and "blocked" in users[username]:
            users[username]["blocked"] = False
            f.seek(0)
            json.dump(users, f, ensure_ascii=False, indent=2)
            f.truncate()
    return RedirectResponse("/admin", status_code=302)

@app.post("/admin/change-password")
async def change_admin_password(request: Request, old_password: str = Form(...), new_password: str = Form(...)):
    with open(USER_DATA_FILE, "r+", encoding="utf-8") as f:
        users = json.load(f)
        if users["admin"]["password"] == hash_password(old_password):
            users["admin"]["password"] = hash_password(new_password)
            f.seek(0)
            json.dump(users, f, ensure_ascii=False, indent=2)
            f.truncate()
    return RedirectResponse("/admin", status_code=302)

@app.get("/admin/export-csv")
async def export_csv():
    if not os.path.exists(USER_DATA_FILE):
        return HTMLResponse("Keine Nutzerdaten vorhanden.")

    with open(USER_DATA_FILE, "r", encoding="utf-8") as f:
        users = json.load(f)

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Benutzername", "Passwort-Hash", "Sicherheitsfrage", "Antwort", "Gesperrt?"])

    for name, data in users.items():
        writer.writerow([
            name,
            data.get("password", ""),
            data.get("question", ""),
            data.get("answer", ""),
            data.get("blocked", False)
        ])

    output.seek(0)
    return StreamingResponse(output, media_type="text/csv", headers={"Content-Disposition": "attachment; filename=users.csv"})

# ✅ Nutzer blockieren oder freischalten
@app.post("/admin/toggle-block")
async def toggle_block(request: Request, username: str = Form(...)):
    if not os.path.exists(USER_DATA_FILE):
        return RedirectResponse("/admin", status_code=302)
    
    with open(USER_DATA_FILE, "r", encoding="utf-8") as f:
        users = json.load(f)
    
    if username in users and username != "admin":
        users[username]["blocked"] = not users[username].get("blocked", False)
        with open(USER_DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(users, f, ensure_ascii=False, indent=2)
    
    return RedirectResponse("/admin", status_code=302)

# 📂 Verlauf anzeigen
@app.get("/admin/history/{username}", response_class=HTMLResponse)
async def view_history(request: Request, username: str):
    path = f"user_chats/{username}.json"
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            history = json.load(f)
    else:
        history = []

    return templates.TemplateResponse("admin_history.html", {"request": request, "username": username, "history": history})

# ❌ Verlauf löschen
@app.post("/admin/delete-history")
async def delete_history(username: str = Form(...)):
    path = f"user_chats/{username}.json"
    if os.path.exists(path):
        os.remove(path)
    return RedirectResponse(f"/admin/history/{username}", status_code=302)

# 🔐 Nutzer-Passwort ändern
@app.post("/admin/change-user-password")
async def change_user_password(username: str = Form(...), new_password: str = Form(...)):
    if not os.path.exists(USER_DATA_FILE):
        return RedirectResponse("/admin", status_code=302)

    with open(USER_DATA_FILE, "r", encoding="utf-8") as f:
        users = json.load(f)

    if username in users and username != "admin":
        users[username]["password"] = hash_password(new_password)
        with open(USER_DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(users, f, ensure_ascii=False, indent=2)

    return RedirectResponse("/admin", status_code=302)
