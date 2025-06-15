from fastapi import FastAPI
from fastapi.responses import PlainTextResponse
from ollama_chat import get_response
from fastapi import Request

app = FastAPI()

@app.get("/")
def root():
    return PlainTextResponse("Hi Babe, ich bin online 💖")
@app.post("/chat")
async def chat(req: Request):
    data = await req.json()
    user_message = data.get("message", "")
    response = get_response(user_message)
    return {"reply": response}
