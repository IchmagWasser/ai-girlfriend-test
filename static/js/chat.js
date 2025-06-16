async function sendMessage() {
    const input = document.getElementById("input");
    const chat = document.getElementById("chat");
    const message = input.value;

    if (!message.trim()) return;

    chat.innerHTML += `<div><strong>Du:</strong> ${message}</div>`;
    input.value = "";

    const res = await fetch("/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message })
    });

    const data = await res.json();
    chat.innerHTML += `<div><strong>Sie:</strong> ${data.reply}</div>`;
}
