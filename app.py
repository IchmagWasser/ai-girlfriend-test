from fastapi import FastAPI
from fastapi.responses import PlainTextResponse

app = FastAPI()

@app.get("/")
def root():
    return PlainTextResponse("Hi Babe, ich bin online 💖")
