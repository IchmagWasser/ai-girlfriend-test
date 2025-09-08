const form = document.getElementById("chat-form");
const input = document.getElementById("user-input");
const chat = document.getElementById("chat");

form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const userText = input.value.trim();
    if (!userText) return;

    appendMessage("Du", userText);
    input.value = "";

    // Ladeanzeige
    const loader = appendMessage("KI", "Antwort wird geladen...");

    try {
        const res = await fetch("/chat", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ message: userText })
        });

        const data = await res.json();
        loader.innerHTML = `<strong>KI:</strong> ${data.reply}`;
    } catch (error) {
        loader.innerHTML = `<strong>Fehler:</strong> Nachricht konnte nicht gesendet werden.`;
    }
});

// Funktion zum Nachrichtenanhängen
function appendMessage(sender, text) {
    const msgDiv = document.createElement("div");
    msgDiv.classList.add("message", sender === "Du" ? "user-message" : "ai-message");
    msgDiv.innerHTML = `<strong>${sender}:</strong> ${text}`;
    chat.appendChild(msgDiv);
    chat.scrollTop = chat.scrollHeight;
    return msgDiv;
}
