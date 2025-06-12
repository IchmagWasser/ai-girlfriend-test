from openai import OpenAI
import os
from dotenv import load_dotenv

load_dotenv()  # <-- lädt die .env Datei automatisch

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

def ask_gpt(prompt):
    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": "Du bist eine hilfreiche KI."},
            {"role": "user", "content": prompt}
        ]
    )
    return response.choices[0].message.content
