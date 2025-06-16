import os
from dotenv import load_dotenv
import requests
import openai

load_dotenv()

USE_OPENAI = os.getenv("USE_OPENAI", "false").lower() == "true"
OPENAI_KEY = os.getenv("OPENAI_API_KEY", "")
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")

openai.api_key = OPENAI_KEY

def get_response(message: str) -> str:
    if USE_OPENAI and OPENAI_KEY:
        try:
            completion = openai.ChatCompletion.create(
                model="gpt-3.5-turbo",
                messages=[{"role": "user", "content": message}]
            )
            return completion.choices[0].message["content"]
        except Exception as e:
            return f"OpenAI error: {str(e)}"

    # Fallback: lokale Ollama-KI (z. B. llama3)
    try:
        response = requests.post(
            f"{OLLAMA_URL}/api/chat",
            json={
                "model": "llama3",
                "messages": [{"role": "user", "content": message}]
            },
            timeout=30
        )
        response.raise_for_status()
        return response.json()["message"]["content"]
    except Exception as e:
        return f"Ollama error: {str(e)}"
