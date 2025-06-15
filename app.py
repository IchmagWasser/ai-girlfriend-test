from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from ollama_chat import get_response

app = FastAPI()

# Static-Files-Ordner für CSS/JS
app.mount("/static", StaticFiles(directory="static"), name="static")

# Template-Ordner für HTML
templates = Jinja2Templates(directory="templates")

# Startseite (liefert chat.html aus)
@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    return templates.TemplateResponse("chat.html", {"request": request})

# Chat-Endpoint für Nachrichten (wird per JS aufgerufen)
@app.post("/chat")
async def chat(req: Request):
    data = await req.json()
    user_message = data.get("message", "")
    response = get_response(user_message)
    return {"reply": response}
