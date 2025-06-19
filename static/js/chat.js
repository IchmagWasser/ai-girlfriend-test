const form = document.getElementById("chat-form");
const input = document.getElementById("user-input");
const chat = document.getElementById("chat");

form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const userText = input.value.trim();
    if (!userText) return;

    appendMessage("Du", userText);
    input.value = "";

    try {
        const res = await fetch("/chat", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ message: userText })
        });
        const data = await res.json();
        appendMessage("Sie", data.reply);
    } catch (error) {
        appendMessage("Fehler", "Nachricht konnte nicht gesendet werden.");
    }
});

function appendMessage(sender, text) {
    const msgDiv = document.createElement("div");
    msgDiv.classList.add("message", sender === "Du" ? "user-message" : "ai-message");
    msgDiv.innerHTML = `<strong>${sender}:</strong> ${text}`;
    chat.appendChild(msgDiv);
    chat.scrollTop = chat.scrollHeight;
}
