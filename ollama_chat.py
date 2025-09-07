import os
from dotenv import load_dotenv
import requests
import openai

load_dotenv()

USE_OPENAI = os.getenv("USE_OPENAI", "false").lower() == "true"
OPENAI_KEY = os.getenv("OPENAI_API_KEY", "")
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")

openai.api_key = OPENAI_KEY

def get_response_with_messages(messages: list) -> str:
    """
    Erweiterte Ollama-Integration mit Message-History
    """
    if USE_OPENAI and OPENAI_KEY:
        try:
            completion = openai.ChatCompletion.create(
                model="gpt-3.5-turbo",
                messages=messages,
                max_tokens=500
            )
            return completion.choices[0].message["content"]
        except Exception as e:
            return f"OpenAI error: {str(e)}"

    # Ollama mit Message-History
    try:
        # Erst prüfen ob Ollama läuft
        response = requests.get(f"{OLLAMA_URL}/api/tags", timeout=5)
        if response.status_code != 200:
            return "❌ Ollama Server ist nicht verfügbar. Starte Ollama mit: `ollama serve`"
        
        # Chat Request an Ollama
        response = requests.post(
            f"{OLLAMA_URL}/api/chat",
            json={
                "model": "llama3.2",  # oder llama3 falls du das hast
                "messages": messages,
                "stream": False
            },
            timeout=30
        )
        
        if response.status_code == 200:
            result = response.json()
            return result.get("message", {}).get("content", "Keine Antwort erhalten")
        else:
            return f"❌ Ollama Fehler: {response.status_code} - {response.text}"
            
    except requests.exceptions.ConnectionError:
        return "❌ Ollama ist nicht erreichbar. Starte Ollama mit: `ollama serve`"
    except requests.exceptions.Timeout:
        return "❌ Ollama antwortet nicht (Timeout). Versuche es erneut."
    except Exception as e:
        return f"❌ Unbekannter Fehler: {str(e)}"

def get_response(message: str) -> str:
    """
    Fallback-Funktion für einzelne Nachrichten ohne Kontext
    (für Kompatibilität mit altem Code)
    """
    messages = [
        {"role": "system", "content": "Du bist ein hilfsfreundlicher KI-Assistent. Antworte auf Deutsch."},
        {"role": "user", "content": message}
    ]
    return get_response_with_messages(messages)