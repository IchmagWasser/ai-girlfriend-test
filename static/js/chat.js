async function sendMessage() {
    const input = document.getElementById("input");
    const message = input.value;
    input.value = "";

    document.getElementById("chat").innerHTML += `<div class="user">Du: ${message}</div>`;

    const response = await fetch("/ask", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({message})
    });

    const data = await response.json();
    document.getElementById("chat").innerHTML += `<div class="ai">Sie: ${data.response}</div>`;
}
