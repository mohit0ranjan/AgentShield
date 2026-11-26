const byId = (id) => document.getElementById(id);
const messages = byId("messages");
const conversation = byId("conversation");
const timeline = byId("timeline");
const input = byId("prompt");
const send = byId("send");
const exporter = byId("download");
const runSummary = byId("run-summary");
const traceBody = document.querySelector(".activity-body");
const chatStatus = byId("chat-status");
const states = {
  allowed: "Passed", blocked: "Blocked", skipped: "Skipped", quarantined: "Quarantined",
  started: "Started", info: "Recorded", error: "Error", redacted: "Redacted",
};
let selectedRun;
let conversationId = null;
let busy = false;
let ready = false;
let uploading = false;
let chatAttachment = null;
let knowledgePreference = false;
const sourceError = byId("source-error");

function element(tag, text = "", className = "") {
  const node = document.createElement(tag);
  node.textContent = text;
  node.className = className;
  return node;
}

function pill(label, tone) {
  return element("span", label, "pill " + tone);
}

function setGate(label, tone, description) {
  const status = byId("gate-status");
  status.textContent = label;
  status.className = "pill " + tone;
  byId("gate-description").textContent = description;
}

function showError(message) {
  byId("error").textContent = message;
  byId("error").hidden = !message;
}

function scrollChat() {
  conversation.scrollTop = conversation.scrollHeight;
}

function setBusy(value) {
  busy = value;
  const locked = value || uploading;
  send.disabled = locked || !ready;
  byId("reset").disabled = locked;
  input.readOnly = value;
  byId("attach-document").disabled = locked || !ready;
  byId("knowledge-nav").disabled = locked;
  byId("use-knowledge").disabled = locked || Boolean(chatAttachment);
  for (const button of byId("chat-attachment").querySelectorAll("button")) button.disabled = locked;
  for (const button of document.querySelectorAll(".thread, .example-item")) {
    button.disabled = locked;
  }
}

function addPrompt(text, attachmentName) {
  byId("welcome").hidden = true;
  const row = element("div", "", "message user-message");
  const bubble = element("div", "", "bubble");
  if (attachmentName) bubble.append(element("p", attachmentName, "message-attachment"));
  bubble.append(element("p", text));
  row.append(bubble);
  messages.append(row);
}

function addAnswer(run) {
  const row = element("article", "", "message");
  const avatar = element("span", "✓", "avatar");
  avatar.setAttribute("aria-hidden", "true");
  const body = element("div", "", "answer-body");
  const heading = element("div", "", "answer-meta");
  const source = run.status === "Blocked" ? "Gateway decision" : run.mode;
  heading.append(element("strong", "AgentShield"), element("small", source));
  if (run.trace.some((event) => event.title === "Output check" && event.status === "redacted")) {
    heading.append(pill("Output redacted", "redacted"));
  }
  const reply = element("p", "", "bubble reply-text");
  for (const fragment of run.reply.split(/(\*\*[^*\n]+\*\*|\x60[^\x60\n]+\x60)/g)) {
    if (fragment.startsWith("**") && fragment.endsWith("**") && fragment.length > 4) {
      reply.append(element("strong", fragment.slice(2, -2)));
    } else if (fragment.startsWith(String.fromCharCode(96)) && fragment.endsWith(String.fromCharCode(96)) && fragment.length > 2) {
      reply.append(element("code", fragment.slice(1, -1)));
    } else {
      reply.append(document.createTextNode(fragment));
    }
  }
  body.append(heading, reply);
  if (run.sources.length) {
    const citations = element("div", "", "citations");
    for (const source of run.sources) {
      const details = element("details", "", "citation");
      details.append(element("summary", "[" + source.ref + "] " + source.name + " · " + source.location),
        element("p", source.text));
      citations.append(details);
    }
    body.append(citations);
  }
  const inspect = element("button", "Inspect this run", "inspect");
  inspect.type = "button";
  inspect.addEventListener("click", () => {
    if (busy) return;
    showRun(run);
    showActivity();
  });
  body.append(inspect);
  row.append(avatar, body);
  messages.append(row);
  scrollChat();
}

function appendEvent(item) {
  if (item.title === "Prompt screening" && item.status === "blocked") {
    setGate("Blocked", "blocked", "AgentShield stopped the prompt locally before sending any request to Nemotron.");
  } else if (item.title === "Prompt screening" && item.status === "allowed") {
    setGate("Passed", "allowed", "The local checks did not flag the prompt. It is eligible for inference.");
  } else if (item.title === "Attachment screening" && item.status === "blocked") {
    setGate("Attachment blocked", "blocked", "AgentShield stopped the attached document before contacting NVIDIA.");
  } else if (item.title === "Model request" && item.status === "started") {
    setGate("Sending", "started", "Local screening passed. AgentShield is contacting NVIDIA NIM.");
  } else if (item.title === "Model response" && item.status === "allowed") {
    setGate("Forwarded", "allowed", "Local screening passed and Nemotron responded.");
  } else if (item.title === "Provider response" && item.status === "error" &&
             byId("gate-status").textContent === "Sending") {
    setGate("Request failed", "error", "Local screening passed. The NVIDIA request failed.");
  }
  const row = element("li", "", "event " + item.status);
  const header = element("div", "", "event-title");
  header.append(element("strong", item.title), pill(states[item.status], item.status),
    element("span", item.elapsed_ms.toFixed(1) + " ms", "event-time"));
  row.append(header, element("p", item.detail));
  if (item.data) {
    const details = element("details");
    const sourceCheck = item.title === "Source verification" || item.title === "Attachment screening";
    const outputCheck = item.title === "Output check";
    const summary = sourceCheck ? "Review source decisions" :
      outputCheck ? "Review output checks" : Array.isArray(item.data) ? "View checks" : "View details";
    details.append(element("summary", summary));
    if (outputCheck) {
      details.open = item.status === "redacted";
      const list = element("ul", "", "check-list");
      for (const check of item.data.checks) {
        const line = element("li");
        const heading = element("div", "", "check-header");
        heading.append(element("strong", check.name), pill(check.count ? String(check.count) + " replaced" : "No match",
          check.count ? "redacted" : "allowed"));
        line.append(heading);
        list.append(line);
      }
      details.append(list);
    } else if (Array.isArray(item.data)) {
      details.open = item.status === "blocked";
      details.append(element("p", "AgentShield normalizes the text and runs local pattern and heuristic checks.", "check-evidence"));
      const checks = element("ul", "", "check-list");
      for (const check of item.data) {
        const line = element("li");
        const header = element("div", "", "check-header");
        const passed = check.status === "pass";
        header.append(element("strong", check.name), pill(passed ? "Passed" : "Matched", passed ? "allowed" : "blocked"));
        line.append(header);
        if (check.category) line.append(element("span", check.category, "check-category"));
        const evidence = [check.description, check.evidence || check.reason].filter(Boolean).join(" ");
        if (evidence) line.append(element("p", evidence, "check-evidence"));
        checks.append(line);
      }
      details.append(checks);
    } else if (sourceCheck) {
      details.open = item.status === "quarantined" || item.status === "blocked";
      const list = element("ul", "", "check-list");
      for (const source of item.data.selected) {
        const line = element("li");
        line.append(pill("Verified", "allowed"), element("p",
          source.ref ? "[" + source.ref + "] " + source.name + " · " + source.location : source.name, "check-evidence"));
        list.append(line);
      }
      for (const source of item.data.withheld) {
        const line = element("li");
        line.append(pill("Withheld", "quarantined"), element("p", source.name, "source-check-name"),
          element("p", source.reason, "check-evidence"));
        for (const flag of source.findings || []) {
          line.append(element("p", flag.name + " · " + flag.location, "check-evidence"));
        }
        list.append(line);
      }
      details.append(list);
    } else {
      details.append(element("pre", JSON.stringify(item.data, null, 2)));
    }
    row.append(details);
  }
  timeline.append(row);
}

function resetTrace() {
  selectedRun = undefined;
  timeline.replaceChildren();
  runSummary.replaceChildren();
  runSummary.hidden = true;
  exporter.disabled = true;
  byId("empty-activity").hidden = false;
  chatStatus.hidden = true;
  setGate("Awaiting input", "skipped", "AgentShield normalizes and screens each prompt locally. Only prompts that pass can reach NVIDIA.");
}

function showRun(run, keepEvents = false) {
  selectedRun = run;
  byId("empty-activity").hidden = true;
  runSummary.hidden = false;
  exporter.disabled = false;
  let tone = "skipped";
  if (run.status === "Blocked" || run.status.endsWith("error")) tone = "blocked";
  if (run.status === "Answered") tone = "allowed";
  const summary = element("div", "", "summary-line");
  summary.append(pill(run.status, tone), element("span", run.mode, "summary-meta"));
  runSummary.replaceChildren(summary, element("p",
    run.model_requests + " model request(s) · " + run.tools_executed + " tool(s) executed · " +
    run.duration_ms.toFixed(1) + " ms", "summary-meta"));
  chatStatus.replaceChildren(pill(run.status, tone));
  chatStatus.hidden = false;
  if (!keepEvents) {
    timeline.replaceChildren();
    setGate("Awaiting input", "skipped", "The selected run's local screening result appears here.");
    run.trace.forEach(appendEvent);
    traceBody.scrollTop = 0;
  }
}

async function readRun(body) {
  const response = await fetch("/api/chat", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  if (!response.ok) throw new Error((await response.json()).error || "The request could not start.");
  const stream = response.body.pipeThrough(new TextDecoderStream()).getReader();
  let pending = "";
  let run;

  function accept(line) {
    if (!line.trim()) return;
    const message = JSON.parse(line);
    if (message.type === "event") appendEvent(message.event);
    if (message.type === "conversation") conversationId = message.id;
    if (message.type === "complete") run = message.result;
  }

  while (true) {
    const chunk = await stream.read();
    if (chunk.done) break;
    pending += chunk.value;
    const lines = pending.split("\n");
    pending = lines.pop();
    lines.forEach(accept);
  }
  accept(pending);
  if (!run) throw new Error("The server ended this request before reporting a result.");
  return run;
}

async function submit(prompt) {
  if (busy || uploading || !ready || !prompt.trim()) return;
  const attached = chatAttachment;
  setBusy(true);
  resetTrace();
  setGate("Checking", "started", "AgentShield is inspecting the prompt before it can reach Nemotron.");
  byId("empty-activity").hidden = true;
  chatStatus.replaceChildren(pill("Checking request", "started"));
  chatStatus.hidden = false;
  showError("");
  input.value = "";
  addPrompt(prompt, attached?.name);
  scrollChat();
  try {
    const knowledge = byId("use-knowledge").checked;
    const run = await readRun({ prompt, knowledge, conversation_id: conversationId, source_id: attached?.id ?? null });
    showRun(run, true);
    addAnswer(run);
    setAttachment(null);
    await refreshThreads().catch(() => {});
  } catch (failure) {
    showError(failure.message);
    chatStatus.replaceChildren(pill("Request failed", "error"));
    byId("empty-activity").hidden = timeline.children.length > 0;
  } finally {
    setBusy(false);
  }
}

function showActivity() {
  if (window.matchMedia("(max-width: 640px)").matches) {
    byId("activity").scrollIntoView({ block: "start" });
  }
  byId("activity").focus({ preventScroll: true });
}

byId("chat-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const prompt = input.value.trim();
  if (busy || uploading || !prompt) return;
  const email = parseEmailCommand(prompt);
  if (email) {
    if (chatAttachment) {
      showError("Remove the chat attachment before composing a plain-text email.");
      return;
    }
    startEmailCommand(email);
    return;
  }
  submit(prompt);
});

input.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    byId("chat-form").requestSubmit();
  }
});

byId("reset").addEventListener("click", () => {
  if (busy || uploading) return;
  conversationId = null;
  setLibrary(false);
  messages.replaceChildren();
  setAttachment(null);
  resetTrace();
  markThread();
  byId("welcome").hidden = false;
  input.value = "";
  showError("");
  input.focus();
});

exporter.addEventListener("click", () => {
  if (!selectedRun) return;
  const { reply, sources, ...run } = selectedRun;
  const report = { ...run, sources: sources.map(({ text, ...source }) => source) };
  const blob = new Blob([JSON.stringify(report, null, 2) + "\n"], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = "agentshield-run.json";
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
});

byId("jump-trace").addEventListener("click", showActivity);

for (const example of byId("prompt-examples").querySelectorAll("button[data-prompt]")) {
  example.addEventListener("click", () => {
    if (busy || uploading) return;
    input.value = example.dataset.prompt;
    setAttachment(null);
    byId("use-knowledge").checked = false;
    byId("prompt-examples").open = false;
    input.focus();
  });
}

const emailDialog = byId("email-dialog");
const emailInstruction = byId("email-instruction");
const emailRecipient = byId("email-recipient");
const emailSubject = byId("email-subject");
const emailBody = byId("email-body");
const emailPreview = byId("email-preview");
const emailConfirm = byId("confirm-email");
let emailReview = null;
let emailBusy = false;
let deliveryPending = false;
const gmailSettings = byId("gmail-settings");
const gmailFields = byId("gmail-fields");
const gmailAddress = byId("gmail-address");
const gmailPassword = byId("gmail-password");
const disconnectGmail = byId("disconnect-gmail");
let gmailAvailable = true;

function parseEmailCommand(prompt) {
  const addressPattern = /[a-z0-9.!#$%&'*+/=?^_~-]+@[a-z0-9-]+(?:\.[a-z0-9-]+)+/gi;
  const addresses = [...new Set(prompt.match(addressPattern) || [])];
  const command = prompt.replace(addressPattern, "RECIPIENT");
  const opening = /^(?:(?:please|pls|hey agentshield|agentshield)[,\s]+)*(?:(?:can|could|would|will) you\s+|i want you to\s+)?/i;
  const request = command.replace(opening, "");
  const directEmail = /^e-?mail\s+(?:to\b|RECIPIENT\b|him\b|her\b|them\b|me\b|about\b|[\p{L}]+\s+(?:about|regarding)\b)/iu;
  const composeEmail = /^(?:send|draft|write|compose)\s+(?:(?:an?|the|one|friendship|friendly|invitation|reminder)\s+)*(?:e-?mail\b|(?:him|her|them|me|RECIPIENT)\s+(?:an?\s+)?e-?mail\b)/i;
  const afterRecipient = /^<?RECIPIENT>?\s*[,:\-]?\s*(?:ko\s+)?(?:(?:send\s+(?:an?\s+)?)?e-?mail|mail)\b/i;
  const hinglish = /^(?:(?:RECIPIENT|[\p{L}\p{M}]+)\s+ko\s+)?(?:ek\s+)?(?:e-?mail|mail)\s+(?:bhej(?:o| do|na)?|kar(?:o| do|na)?|likh(?:o| do|na)?)\b/iu;
  if (![directEmail, composeEmail, afterRecipient, hinglish].some((pattern) => pattern.test(request))) {
    return null;
  }
  return {
    prompt: prompt.replace(addressPattern, "the recipient"),
    recipient: addresses.length === 1 ? addresses[0] : "",
    multipleRecipients: addresses.length > 1,
  };
}

function startEmailCommand(command) {
  if (emailBusy) return;
  clearEmailReview();
  showError("");
  emailInstruction.value = command.prompt;
  emailRecipient.value = command.recipient;
  emailSubject.value = "";
  emailBody.value = "";
  openEmailDialog();
  input.value = "";
  doEmailAction(async () => {
    await generateEmailDraft();
    if (command.recipient) {
      await reviewEmailDraft();
    } else {
      showEmailStatus(command.multipleRecipients
        ? "Choose one recipient, then review the email."
        : "Enter the recipient’s email address, then review the draft.");
      requestAnimationFrame(() => emailRecipient.focus());
    }
  });
}

function showEmailStatus(message, error = false) {
  const status = byId("email-status");
  status.textContent = message;
  status.classList.toggle("error", error);
}

function showEmailChecks(checks) {
  const items = checks.map((check) => {
    const row = element("li");
    const title = element("div", "", "check-header");
    const passed = check.status === "pass";
    title.append(element("strong", check.name), pill(passed ? "Passed" : "Blocked", passed ? "allowed" : "blocked"));
    row.append(title, element("p", check.detail, "check-evidence"));
    return row;
  });
  byId("email-checks").replaceChildren(...items);
  byId("email-security").hidden = items.length === 0;
}

function clearEmailReview() {
  emailReview = null;
  deliveryPending = false;
  emailPreview.hidden = true;
  emailConfirm.hidden = true;
  emailConfirm.textContent = "Confirm & send";
  showEmailChecks([]);
  showEmailStatus("");
}

async function emailRequest(action, data) {
  const response = await fetch("/api/email/" + action, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data),
  });
  const result = await response.json();
  if (!response.ok) {
    showEmailChecks(result.checks || []);
    const failure = new Error(result.error);
    failure.rejected = true;
    throw failure;
  }
  if (result.checks?.length) showEmailChecks(result.checks);
  return result;
}

async function doEmailAction(action) {
  if (emailBusy) return;
  emailBusy = true;
  byId("email-fields").disabled = true;
  gmailFields.disabled = true;
  emailConfirm.disabled = true;
  byId("close-email").disabled = true;
  try {
    await action();
  } catch (failure) {
    showEmailStatus(failure.message, true);
  } finally {
    emailBusy = false;
    byId("email-fields").disabled = false;
    gmailFields.disabled = !gmailAvailable;
    emailConfirm.disabled = false;
    byId("close-email").disabled = false;
  }
}

function showGmailStatus(message, error = false) {
  const status = byId("gmail-account-status");
  status.textContent = message;
  status.classList.toggle("error", error);
}

function renderGmailAccount(account) {
  gmailAvailable = account.setup_available;
  gmailFields.disabled = emailBusy || !gmailAvailable;
  disconnectGmail.hidden = !account.gmail;
  if (account.gmail && !gmailAddress.matches(":focus")) gmailAddress.value = account.gmail;
  showGmailStatus(account.gmail ? "Connected as " + account.gmail
    : account.sender ? "Sending configured as " + account.sender
    : gmailAvailable ? "No Gmail account connected." : "In-app Gmail setup requires Windows.");
}

async function refreshGmailAccount() {
  try {
    renderGmailAccount(await jsonRequest("/api/email/config"));
  } catch {
    showGmailStatus("Could not load the sending account.", true);
  }
}

function openEmailDialog(showGmail = false) {
  gmailSettings.open = showGmail;
  emailDialog.showModal();
  refreshGmailAccount();
  (showGmail ? gmailAddress : emailInstruction).focus();
}

byId("open-email").addEventListener("click", () => {
  if (!emailInstruction.value && input.value.trim()) emailInstruction.value = input.value.trim().slice(0, 2000);
  openEmailDialog();
});
byId("open-gmail").addEventListener("click", () => openEmailDialog(true));
byId("close-email").addEventListener("click", () => emailDialog.close());
emailDialog.addEventListener("close", () => { gmailPassword.value = ""; });

byId("gmail-form").addEventListener("submit", (event) => {
  event.preventDefault();
  doEmailAction(async () => {
    const refreshReview = emailReview && !deliveryPending;
    showGmailStatus("Verifying Gmail over TLS…");
    try {
      const account = await jsonRequest("/api/email/gmail", {
        address: gmailAddress.value.trim(), password: gmailPassword.value,
      });
      gmailPassword.value = "";
      renderGmailAccount(account);
    } catch (failure) {
      gmailPassword.value = "";
      showGmailStatus(failure.message, true);
      return;
    }
    if (refreshReview) await reviewEmailDraft();
  });
});

disconnectGmail.addEventListener("click", () => doEmailAction(async () => {
  try {
    const account = await jsonRequest("/api/email/gmail/disconnect", { confirm: true });
    gmailAddress.value = "";
    renderGmailAccount(account);
    if (!deliveryPending) clearEmailReview();
  } catch (failure) {
    showGmailStatus(failure.message, true);
  }
}));
emailDialog.addEventListener("cancel", (event) => {
  if (emailBusy) event.preventDefault();
});
for (const field of [emailRecipient, emailSubject, emailBody]) {
  field.addEventListener("input", clearEmailReview);
}

async function generateEmailDraft() {
  clearEmailReview();
  showEmailStatus("Generating a draft…");
  const draft = await emailRequest("draft", { prompt: emailInstruction.value });
  emailSubject.value = draft.subject;
  emailBody.value = draft.body;
}

async function reviewEmailDraft() {
  emailReview = null;
  emailPreview.hidden = true;
  emailConfirm.hidden = true;
  deliveryPending = false;
  emailConfirm.textContent = "Confirm & send";
  showEmailStatus("Checking the recipient and message…");
  emailReview = await emailRequest("review", {
    recipient: emailRecipient.value.trim(), subject: emailSubject.value, body: emailBody.value,
  });
  byId("preview-sender").textContent = emailReview.sender || "Sending account not configured";
  byId("preview-recipient").textContent = emailReview.recipient;
  byId("preview-subject").textContent = emailReview.subject;
  byId("preview-body").textContent = emailReview.body;
  emailPreview.hidden = false;
  emailConfirm.hidden = !emailReview.token;
  showEmailStatus(emailReview.token
    ? "Check the preview, then confirm to send. This review is valid for 10 minutes."
    : "Draft passed. Open Gmail sending account to enable delivery.");
  requestAnimationFrame(() => emailPreview.scrollIntoView({ block: "nearest" }));
}

byId("generate-email").addEventListener("click", () => {
  if (!emailInstruction.value.trim()) {
    showEmailStatus("Describe the email you want to draft.", true);
    emailInstruction.focus();
    return;
  }
  doEmailAction(async () => {
    await generateEmailDraft();
    showEmailStatus("Check the draft and enter a recipient before reviewing.");
    requestAnimationFrame(() => emailRecipient.focus());
  });
});

byId("email-form").addEventListener("submit", (event) => {
  event.preventDefault();
  doEmailAction(reviewEmailDraft);
});

emailConfirm.addEventListener("click", () => doEmailAction(async () => {
  if (!emailReview?.token) return;
  const action = deliveryPending ? "status" : "send";
  showEmailStatus(deliveryPending ? "Checking delivery status…" : "Rechecking and submitting the email…");
  let result;
  try {
    result = await emailRequest(action, deliveryPending
      ? { token: emailReview.token }
      : { token: emailReview.token, confirm: true });
  } catch (failure) {
    if (failure.rejected) {
      emailConfirm.hidden = true;
      emailReview = null;
      throw failure;
    }
    deliveryPending = true;
    emailConfirm.textContent = "Check delivery status";
    showEmailStatus("Connection interrupted. Check the delivery status before starting a new review.", true);
    return;
  }
  const messages = {
    sent: "Email accepted by the SMTP server.",
    failed: "The email could not be sent. Check the sending account before reviewing again.",
    uncertain: "Delivery could not be confirmed. Check your mail account before sending again.",
    sending: "Delivery is still in progress. Check the status again shortly.",
    ready: "Delivery did not start. Return to the form and review again to send.",
  };
  showEmailStatus(messages[result.status], ["failed", "uncertain"].includes(result.status));
  deliveryPending = result.status === "sending";
  emailConfirm.hidden = !deliveryPending;
  emailConfirm.textContent = "Check delivery status";
  if (!deliveryPending) emailReview = null;
}));

async function jsonRequest(path, data) {
  const response = await fetch(path, data === undefined ? undefined : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data),
  });
  const result = await response.json();
  if (!response.ok) throw new Error(result.error);
  return result;
}

function markThread() {
  for (const button of document.querySelectorAll(".thread")) {
    button.setAttribute("aria-current", String(button.dataset.thread === conversationId));
  }
}

async function refreshThreads() {
  const threads = await jsonRequest("/api/conversations");
  const items = threads.map((thread) => {
    const button = element("button", thread.title, "demo thread");
    button.type = "button";
    button.title = thread.title;
    button.dataset.thread = thread.id;
    button.disabled = busy;
    button.addEventListener("click", () => loadThread(thread.id));
    return button;
  });
  byId("threads").replaceChildren(...(items.length ? items : [element("p", "No saved chats yet.", "empty-list")]));
  markThread();
}

async function loadThread(identifier) {
  if (busy || uploading) return;
  setBusy(true);
  setLibrary(false);
  showError("");
  try {
    const thread = await jsonRequest("/api/conversations/" + identifier);
    conversationId = thread.id;
    messages.replaceChildren();
    setAttachment(null);
    resetTrace();
    byId("welcome").hidden = thread.turns.length > 0;
    for (const turn of thread.turns) {
      addPrompt(turn.prompt, turn.result.attachment?.name);
      addAnswer(turn.result);
    }
    if (thread.turns.length) showRun(thread.turns.at(-1).result);
    markThread();
  } catch (failure) {
    showError(failure.message);
  } finally {
    setBusy(false);
  }
}

function setLibrary(open) {
  document.querySelector(".chat").hidden = open;
  byId("activity").hidden = open;
  byId("library").hidden = !open;
  byId("knowledge-nav").setAttribute("aria-current", open ? "page" : "false");
}

function setSourceError(message) {
  sourceError.textContent = message;
  sourceError.hidden = !message;
}

function sourceState(state) {
  const labels = { approved: "Approved", pending: "Awaiting review", archived: "Archived", quarantined: "Quarantined" };
  const tone = { approved: "allowed", pending: "skipped", archived: "skipped", quarantined: "quarantined" };
  return pill(labels[state], tone[state]);
}

async function refreshSources() {
  const sources = await jsonRequest("/api/documents");
  byId("source-counts").textContent = sources.length ? [
    sources.filter((source) => source.state === "approved").length + " approved",
    sources.filter((source) => source.state === "quarantined").length + " quarantined",
  ].join(" · ") : "";
  const rows = sources.map((source) => {
    const row = element("button", "", "button source-row");
    row.type = "button";
    row.append(element("span", source.name), sourceState(source.state));
    row.addEventListener("click", () => reviewSource(source.id));
    return row;
  });
  byId("source-list").replaceChildren(...(rows.length ? rows : [element("p", "No sources uploaded.", "empty-list")]));
}

let sourceSelection = 0;
async function reviewSource(identifier) {
  const selection = ++sourceSelection;
  setSourceError("");
  try {
    const source = await jsonRequest("/api/documents/" + identifier);
    if (selection !== sourceSelection) return;
    if (chatAttachment?.id === identifier) setAttachment(source);
    const actions = element("div", "", "review-actions");
    const choices = { pending: [["Approve source", "approved"], ["Archive", "archived"]],
      approved: [["Revoke approval", "pending"], ["Archive", "archived"]],
      quarantined: [["Archive", "archived"]],
      archived: [["Restore for review", "pending"]] };
    for (const [label, state] of choices[source.state]) {
      const button = element("button", label, "button");
      button.type = "button";
      button.addEventListener("click", async () => {
        for (const action of actions.children) action.disabled = true;
        try {
          await jsonRequest("/api/documents/" + identifier + "/state", { state });
          await refreshSources();
          if (selection === sourceSelection) await reviewSource(identifier);
        } catch (failure) {
          setSourceError(failure.message);
        } finally {
          for (const action of actions.children) action.disabled = false;
        }
      });
      actions.append(button);
    }
    const scan = element("section", "", "document-scan");
    const flagged = source.findings.length > 0;
    scan.append(element("h3", "Document security scan"),
      element("p", source.passages.length + (source.passages.length === 1 ? " passage screened" : " passages screened") +
        (source.scanned_at ? " · " + new Date(source.scanned_at).toLocaleString() : "")),
      flagged ? element("p", "AgentShield found possible document injection. Upload a clean revision to use this source in chat.") :
        element("p", "No document-injection patterns matched. Approval is required before the source can enter model context."));
    if (flagged) {
      const flags = element("ul", "", "check-list");
      for (const finding of source.findings) {
        const item = element("li");
        item.append(element("strong", finding.name),
          element("span", finding.location + (finding.passage ? " · Passage " + finding.passage : ""), "check-category"),
          element("p", finding.description, "check-evidence"));
        flags.append(item);
      }
      scan.append(flags);
    }
    const extracted = source.passages.map((part) => part.location + "\n" + part.text).join("\n\n");
    const preview = element("details", "", "source-preview");
    preview.open = !flagged;
    preview.append(element("summary", "Extracted document text"), element("pre", extracted));
    byId("review").replaceChildren(element("h2", source.name), sourceState(source.state), scan, actions, preview);
  } catch (failure) {
    if (selection === sourceSelection) setSourceError(failure.message);
  }
}

byId("knowledge-nav").addEventListener("click", async () => {
  setLibrary(true);
  try { await refreshSources(); } catch (failure) { setSourceError(failure.message); }
});
byId("back-chat").addEventListener("click", () => setLibrary(false));

async function uploadDocument(file) {
  if (file.size > 2_000_000) throw new Error("Use a file of up to 2 MB.");
  const contents = await new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result.slice(reader.result.indexOf(",") + 1));
    reader.onerror = () => reject(new Error("The file could not be read."));
    reader.readAsDataURL(file);
  });
  return jsonRequest("/api/documents", { name: file.name, content: contents });
}

function setAttachment(source) {
  const field = byId("use-knowledge");
  if (source && !chatAttachment) knowledgePreference = field.checked;
  if (!source && chatAttachment) field.checked = knowledgePreference;
  chatAttachment = source;
  byId("knowledge-choice").textContent = source ? "Search this attachment" : "Use approved sources";
  const slot = byId("chat-attachment");
  slot.hidden = !source;
  slot.replaceChildren();
  if (source) {
    field.checked = true;
    const labels = {
      approved: "Search will use this document.",
      pending: "Review and approve this document before using it.",
      quarantined: "Blocked before NVIDIA. Review the matched checks.",
      archived: "Restore and review this document before using it.",
    };
    const heading = element("div", "", "attachment-title");
    heading.append(element("strong", source.name), sourceState(source.state));
    const details = element("div", "", "attachment-details");
    details.append(heading, element("p", labels[source.state]));
    if (source.findings.length) {
      const flags = element("details");
      flags.append(element("summary", source.findings.length + " scan finding(s)"));
      const list = element("ul");
      for (const finding of source.findings) list.append(element("li", finding.name + " · " + finding.location));
      flags.append(list);
      details.append(flags);
    }
    const actions = element("div", "", "attachment-actions");
    const review = element("button", "Review", "button");
    review.type = "button";
    review.addEventListener("click", async () => {
      setLibrary(true);
      try {
        await refreshSources();
        await reviewSource(source.id);
      } catch (failure) {
        setSourceError(failure.message);
      }
    });
    const remove = element("button", "Remove", "button");
    remove.type = "button";
    remove.addEventListener("click", () => { setAttachment(null); input.focus(); });
    actions.append(review, remove);
    slot.append(details, actions);
  }
  setBusy(busy);
}

byId("attach-document").addEventListener("click", () => byId("chat-file").click());
byId("chat-file").addEventListener("change", async (event) => {
  const field = event.currentTarget;
  const file = field.files[0];
  if (!file || busy || uploading) return;
  uploading = true;
  setBusy(false);
  showError("");
  const slot = byId("chat-attachment");
  slot.hidden = false;
  slot.replaceChildren(element("p", "Scanning " + file.name + "…"));
  try {
    const { id } = await uploadDocument(file);
    setAttachment(await jsonRequest("/api/documents/" + id));
  } catch (failure) {
    setAttachment(chatAttachment);
    showError(failure.message);
  } finally {
    field.value = "";
    uploading = false;
    setBusy(false);
  }
});

byId("source-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const field = byId("source-file");
  const file = field.files[0];
  if (!file) return;
  const submitButton = event.currentTarget.querySelector("button");
  submitButton.disabled = true;
  setSourceError("");
  try {
    const { id } = await uploadDocument(file);
    field.value = "";
    await refreshSources();
    await reviewSource(id);
  } catch (failure) {
    setSourceError(failure.message);
  } finally {
    submitButton.disabled = false;
  }
});

async function initialize() {
  try {
    const config = await jsonRequest("/api/config");
    byId("model-status").textContent = config.live ? "NVIDIA NIM configured" : "NVIDIA key required";
    await refreshThreads();
    ready = true;
    setBusy(false);
  } catch (failure) {
    byId("model-status").textContent = "Service unavailable";
    showError(failure.message);
  }
}

initialize();
