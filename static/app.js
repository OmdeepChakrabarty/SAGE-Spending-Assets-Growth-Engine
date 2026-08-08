/* app.js
   -------
   No framework, no build step - plain DOM and fetch/EventSource. Two jobs:
     1. Send a chat message, stream the agent's steps in over SSE, and
        render each one as its own bubble as it arrives.
     2. Keep the "Database" panel showing the real transactions table,
        refreshing it after every turn (in case modify_transactions changed
        something) and on demand via the Refresh button.
*/

const chatLog = document.getElementById("chat-log");
const chatForm = document.getElementById("chat-form");
const chatInput = document.getElementById("chat-input");
const sendBtn = document.getElementById("send-btn");
const dbTableBody = document.getElementById("db-table-body");
const dbCount = document.getElementById("db-count");
const refreshBtn = document.getElementById("refresh-btn");
const examples = document.getElementById("examples");

let activeSource = null; // the EventSource for the in-flight turn, if any
let streamingPre = null; // the <pre> currently showing live model tokens

// ---- Chat bubble helpers --------------------------------------------------

function addBubble(role, { title, extraClass } = {}) {
  const bubble = document.createElement("div");
  bubble.className = `bubble ${role}${extraClass ? " " + extraClass : ""}`;

  if (title) {
    const label = document.createElement("div");
    label.className = "bubble-label";
    label.textContent = title;
    bubble.appendChild(label);
  }

  const content = document.createElement("div");
  content.className = "bubble-content";
  bubble.appendChild(content);

  chatLog.appendChild(bubble);
  chatLog.scrollTop = chatLog.scrollHeight;
  return { bubble, content };
}

function addUserMessage(text) {
  const { content } = addBubble("user");
  content.textContent = text;
}

function addCodeBubble(code) {
  const { content } = addBubble("assistant", { title: "🛠️ Code" });
  const pre = document.createElement("pre");
  pre.textContent = code;
  content.appendChild(pre);
}

function addLogBubble(text) {
  const { content } = addBubble("assistant", { title: "📝 Execution Logs" });
  const pre = document.createElement("pre");
  pre.textContent = text;
  content.appendChild(pre);
}

function addErrorBubble(text) {
  const { content } = addBubble("assistant", { title: "⚠️ Error", extraClass: "error" });
  content.textContent = text;
}

function addFinalBubble(text) {
  const { content } = addBubble("assistant", { title: "Answer", extraClass: "final" });
  content.textContent = text;
}

function addChartBubble() {
  const { content } = addBubble("assistant", { title: "📊 Chart" });
  const img = document.createElement("img");
  // Cache-bust so the browser doesn't show a stale chart from a prior turn.
  img.src = "/api/chart?t=" + Date.now();
  img.alt = "Generated chart";
  content.appendChild(img);
}

function startStreamingBubble() {
  const { bubble, content } = addBubble("assistant", { title: "Thinking…", extraClass: "streaming" });
  const pre = document.createElement("pre");
  content.appendChild(pre);
  streamingPre = pre;
  return pre;
}

function clearStreamingBubble() {
  if (streamingPre) {
    streamingPre.closest(".bubble").remove();
    streamingPre = null;
  }
}

// ---- Database panel --------------------------------------------------------

function escapeText(value) {
  const div = document.createElement("div");
  div.textContent = value;
  return div.innerHTML;
}

async function refreshTransactions() {
  try {
    const res = await fetch("/api/transactions");
    const rows = await res.json();
    dbTableBody.innerHTML = "";
    for (const row of rows) {
      const tr = document.createElement("tr");
      const amountClass = row.amount < 0 ? "income" : "expense";
      tr.innerHTML =
        `<td>${row.id}</td>` +
        `<td>${row.date}</td>` +
        `<td>${escapeText(row.merchant)}</td>` +
        `<td class="${amountClass}">${row.amount.toFixed(2)}</td>` +
        `<td>${escapeText(row.category)}</td>`;
      dbTableBody.appendChild(tr);
    }
    dbCount.textContent = `${rows.length} rows`;
  } catch (err) {
    dbCount.textContent = "couldn't load";
  }
}

// ---- Sending a message ------------------------------------------------------

function setInputEnabled(enabled) {
  chatInput.disabled = !enabled;
  sendBtn.disabled = !enabled;
  if (enabled) chatInput.focus();
}

function sendMessage(text) {
  if (!text || activeSource) return;

  addUserMessage(text);
  chatInput.value = "";
  setInputEnabled(false);

  const source = new EventSource("/api/chat?message=" + encodeURIComponent(text));
  activeSource = source;

  source.onmessage = (evt) => {
    const data = JSON.parse(evt.data);

    switch (data.type) {
      case "delta": {
        const pre = streamingPre || startStreamingBubble();
        pre.textContent = data.content;
        chatLog.scrollTop = chatLog.scrollHeight;
        break;
      }
      case "code":
        clearStreamingBubble();
        addCodeBubble(data.content);
        break;
      case "log":
        addLogBubble(data.content);
        break;
      case "plan":
        addLogBubble(data.content);
        break;
      case "error":
        clearStreamingBubble();
        addErrorBubble(data.content);
        break;
      case "final":
        clearStreamingBubble();
        addFinalBubble(data.content);
        break;
      case "chart":
        addChartBubble();
        break;
      case "done":
        source.close();
        activeSource = null;
        setInputEnabled(true);
        refreshTransactions(); // pick up anything modify_transactions changed
        break;
      default:
        break;
    }
  };

  source.onerror = () => {
    // A network-level failure, not an app-level "error" event - fail
    // gracefully instead of leaving the input stuck disabled.
    clearStreamingBubble();
    addErrorBubble("Lost connection to the server. Please try again.");
    source.close();
    activeSource = null;
    setInputEnabled(true);
  };
}

chatForm.addEventListener("submit", (e) => {
  e.preventDefault();
  sendMessage(chatInput.value.trim());
});

examples.addEventListener("click", (e) => {
  if (e.target.classList.contains("example-chip") && !activeSource) {
    sendMessage(e.target.textContent);
  }
});

refreshBtn.addEventListener("click", refreshTransactions);

// Initial load.
refreshTransactions();
