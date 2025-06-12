from flask import Flask, render_template, request, jsonify
from ollama_chat import ask_gpt
import json
from datetime import datetime
import os
from tts_output import speak


app = Flask(__name__)

LOG_PATH = "logs/chatlog.json"
os.makedirs("logs", exist_ok=True)

@app.route("/")
def index():
    return render_template("chat.html")

@app.route("/ask", methods=["POST"])
def ask():
    user_input = request.json.get("message")
    response = ask_gpt(user_input)
    speak(response)


    log_entry = {
        "timestamp": datetime.now().isoformat(),
        "input": user_input,
        "response": response
    }

    with open(LOG_PATH, "a") as f:
        f.write(json.dumps(log_entry) + "\n")

    return jsonify({"response": response})

if __name__ == "__main__":
    app.run(debug=True)
