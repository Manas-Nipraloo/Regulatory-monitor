const runForm = document.querySelector("#runForm");
const runDate = document.querySelector("#runDate");
const siteSelect = document.querySelector("#siteSelect");
const addSource = document.querySelector("#addSource");
const clearSources = document.querySelector("#clearSources");
const selectedSourceList = document.querySelector("#selectedSourceList");
const uploadDrive = document.querySelector("#uploadDrive");
const saveDraft = document.querySelector("#saveDraft");
const sendEmail = document.querySelector("#sendEmail");
const runButton = document.querySelector("#runButton");
const refreshSites = document.querySelector("#refreshSites");
const refreshHistory = document.querySelector("#refreshHistory");
const historyDate = document.querySelector("#historyDate");
const clearHistoryDate = document.querySelector("#clearHistoryDate");
const healthText = document.querySelector("#healthText");
const articleCount = document.querySelector("#articleCount");
const driveStatus = document.querySelector("#driveStatus");
const draftStatus = document.querySelector("#draftStatus");
const emailStatus = document.querySelector("#emailStatus");
const message = document.querySelector("#message");
const resultsBody = document.querySelector("#resultsBody");
const historyBody = document.querySelector("#historyBody");
const liveLog = document.querySelector("#liveLog");
const liveLogStatus = document.querySelector("#liveLogStatus");
const dashboardDateChip = document.querySelector("#dashboardDateChip");
const sidebarToggle = document.querySelector("#sidebarToggle");
const shell = document.querySelector(".shell");
const liveDot = document.querySelector(".pulse-dot");
let selectedSources = [];

function todayIso() {
  return new Date().toISOString().slice(0, 10);
}

function setMessage(text, state = "") {
  message.textContent = text;
  message.className = `message ${state}`.trim();
}

function resetLiveLog(text = "Starting run...") {
  liveLog.textContent = `> ${text}`;
  liveLogStatus.textContent = "Running";
  liveDot?.classList.add("live");
  liveLog.scrollTop = liveLog.scrollHeight;
}

function appendLiveLog(text, state = "") {
  const prefix = state === "success" ? "OK" : state === "error" ? "!" : ">";
  liveLog.textContent += `\n${prefix} ${text}`;
  liveLog.scrollTop = liveLog.scrollHeight;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function formatRunDate(value) {
  if (!value) {
    return "";
  }
  const [year, month, day] = value.split("-");
  return `${day}-${month}-${year}`;
}

function formatRunTime(value) {
  if (Number.isNaN(value.getTime())) {
    return "";
  }
  return value.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function statusClass(status) {
  return status === "empty" ? "status-empty" : status || "";
}

function setSidebarHidden(hidden) {
  shell?.classList.toggle("sidebar-collapsed", hidden);
  document.body.classList.toggle("sidebar-hidden", hidden);
  if (sidebarToggle) {
    sidebarToggle.setAttribute("aria-label", hidden ? "Show controls" : "Hide controls");
    sidebarToggle.setAttribute("title", hidden ? "Show controls" : "Hide controls");
  }
}

async function loadHealth() {
  try {
    const response = await fetch("/health");
    const data = await response.json();
    healthText.textContent = `${data.app} - ${data.status}`;
  } catch {
    healthText.textContent = "Local server unavailable";
  }
}

async function loadSites() {
  siteSelect.innerHTML = '<option value="">Loading...</option>';
  const response = await fetch("/sites");
  const sites = await response.json();
  siteSelect.innerHTML = sites
    .map((site) => `<option value="${escapeHtml(site.remark)}">${escapeHtml(site.remark)}</option>`)
    .join("");
}

async function loadHistory() {
  try {
    const params = new URLSearchParams({ limit: "100" });
    if (historyDate.value) {
      params.set("run_date", historyDate.value);
    }
    const response = await fetch(`/history?${params.toString()}`);
    if (!response.ok) {
      throw new Error(`History failed with ${response.status}`);
    }
    const entries = await response.json();
    renderHistory(entries);
  } catch (error) {
    historyBody.innerHTML = `<tr><td colspan="8" class="empty">${escapeHtml(error.message)}</td></tr>`;
  }
}

function renderHistory(entries) {
  if (!entries.length) {
    historyBody.innerHTML = '<tr><td colspan="8" class="empty">No article history found.</td></tr>';
    return;
  }

  const rows = [];
  for (const entry of entries) {
    const createdAt = new Date(entry.created_at);
    const articles = entry.articles?.length ? entry.articles : [null];
    for (const article of articles) {
      const source = article?.site_remark || (entry.sources?.length ? entry.sources.join(", ") : "All sources");
      const title = article?.title || `${entry.articles_found} article(s) in this run`;
      const publishedDate = article?.published_date ? formatRunDate(article.published_date) : "-";
      const drive = article?.drive_pdf_url
        ? `<a class="drive-status up" href="${escapeHtml(article.drive_pdf_url)}" target="_blank" rel="noreferrer">Open</a>`
        : article?.drive_folder_url
          ? `<a class="drive-status up" href="${escapeHtml(article.drive_folder_url)}" target="_blank" rel="noreferrer">Folder</a>`
          : entry.upload_drive
            ? '<span class="drive-status pending">Pending</span>'
            : '<span class="drive-status pending">Skipped</span>';
      const summary = article?.summary || entry.message || "";
      rows.push(`
        <tr>
          <td>${escapeHtml(formatRunDate(entry.run_date))}</td>
          <td>${escapeHtml(formatRunTime(createdAt))}</td>
          <td class="history-source">${escapeHtml(source)}</td>
          <td class="history-article">${escapeHtml(title)}</td>
          <td>${escapeHtml(publishedDate)}</td>
          <td>${drive}</td>
          <td><span class="status-pill ${escapeHtml(statusClass(entry.status))}">${escapeHtml(entry.status)}</span></td>
          <td class="history-message">${escapeHtml(summary)}</td>
        </tr>
      `);
    }
  }
  historyBody.innerHTML = rows.join("");
}

function renderSelectedSources() {
  if (!selectedSources.length) {
    selectedSourceList.innerHTML = '<div class="empty">No sources selected.</div>';
    return;
  }

  selectedSourceList.innerHTML = selectedSources
    .map(
      (source) => `
        <div class="selected-source">
          <span>${escapeHtml(source)}</span>
          <button class="remove-source" type="button" title="Remove source" data-source="${escapeHtml(source)}">x</button>
        </div>
      `
    )
    .join("");
}

function renderResults(data) {
  articleCount.textContent = data.articles_found;
  driveStatus.textContent = uploadDrive.checked ? "On" : "Off";
  draftStatus.textContent = data.webmail_draft_saved ? "Saved" : data.draft_path ? "Local" : "None";
  emailStatus.textContent = data.email_sent ? "Sent" : "Off";

  if (!data.articles.length) {
    resultsBody.innerHTML = '<tr><td colspan="4" class="empty">No matching PDFs found.</td></tr>';
    return;
  }

  resultsBody.innerHTML = data.articles
    .map((article) => {
      const drive = article.drive_pdf_url
        ? `<a class="drive-link" href="${escapeHtml(article.drive_pdf_url)}" target="_blank" rel="noreferrer">Uploaded</a>`
        : '<span class="drive-status pending">Skipped</span>';
      return `
        <tr>
          <td class="source-cell">${escapeHtml(article.site_remark)}</td>
          <td class="cell-heading">${escapeHtml(article.title)}</td>
          <td class="cell-summary">${escapeHtml(article.summary || "")}</td>
          <td>${drive}</td>
        </tr>
      `;
    })
    .join("");
}

function appendArticle(article) {
  if (resultsBody.querySelector(".empty")) {
    resultsBody.innerHTML = "";
  }
  const drive = article.drive_pdf_url
    ? `<a class="drive-link" href="${escapeHtml(article.drive_pdf_url)}" target="_blank" rel="noreferrer">Uploaded</a>`
    : '<span class="drive-status pending">Skipped</span>';
  resultsBody.insertAdjacentHTML(
    "beforeend",
    `
      <tr>
        <td class="source-cell">${escapeHtml(article.site_remark)}</td>
        <td class="cell-heading">${escapeHtml(article.title)}</td>
        <td class="cell-summary">${escapeHtml(article.summary || "")}</td>
        <td>${drive}</td>
      </tr>
    `
  );
}

function handleStreamEvent(event) {
  if (event.type === "start") {
    appendLiveLog(`Checking ${event.site_count} source(s)...`);
    return;
  }
  if (event.type === "source" && event.status === "checking") {
    setMessage(`Checking ${event.remark}...`, "running");
    appendLiveLog(`Checking ${event.remark}...`);
    return;
  }
  if (event.type === "source" && event.status === "matched") {
    setMessage(`${event.remark}: ${event.count} matching document(s) found.`, "running");
    appendLiveLog(`${event.remark}: ${event.count} matching document(s) found.`);
    return;
  }
  if (event.type === "document" && event.status === "downloading") {
    setMessage(`Downloading ${event.index} of ${event.total}: ${event.title}`, "running");
    appendLiveLog(`Downloading ${event.index} of ${event.total}: ${event.title}`);
    return;
  }
  if (event.type === "document" && event.status === "processing") {
    setMessage(`Generating heading/summary for ${event.index} of ${event.total}: ${event.title}`, "running");
    appendLiveLog(`Generating heading/summary for ${event.index} of ${event.total}: ${event.title}`);
    return;
  }
  if (event.type === "document" && event.status === "skipped_existing") {
    const text = `Skipping ${event.index} of ${event.total}: ${event.title} already exists in Drive.`;
    setMessage(text, "running");
    appendLiveLog(text, "success");
    return;
  }
  if (event.type === "article") {
    articleCount.textContent = event.count;
    appendArticle(event.article);
    setMessage(`PDF ${event.count} ready: ${event.article.title}`, "running");
    appendLiveLog(`PDF ${event.count} ready: ${event.article.title}`, "success");
    return;
  }
  if (event.type === "error") {
    setMessage(event.message, "error");
    appendLiveLog(event.message, "error");
    return;
  }
  if (event.type === "done") {
    articleCount.textContent = event.articles_found;
    draftStatus.textContent = event.webmail_draft_saved ? "Saved" : event.draft_path ? "Local" : "None";
    emailStatus.textContent = event.email_sent ? "Sent" : "Off";
    if (!event.articles_found) {
      resultsBody.innerHTML = '<tr><td colspan="4" class="empty">No matching PDFs found.</td></tr>';
    }
    setMessage(event.message, event.articles_found ? "success" : "");
    liveLogStatus.textContent = "Completed";
    liveDot?.classList.remove("live");
    appendLiveLog(event.message, event.articles_found ? "success" : "");
    loadHistory();
  }
}

runForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const sourcesForRun = selectedSources.length ? selectedSources : [siteSelect.value].filter(Boolean);
  if (!sourcesForRun.length) {
    setMessage("Select at least one source.", "error");
    return;
  }
  runButton.disabled = true;
  articleCount.textContent = "0";
  driveStatus.textContent = uploadDrive.checked ? "On" : "Off";
  draftStatus.textContent = saveDraft.checked ? "On" : "Local";
  emailStatus.textContent = sendEmail.checked ? "On" : "Off";
  resultsBody.innerHTML = '<tr><td colspan="4" class="empty">Running...</td></tr>';
  setMessage("Running source check, download, extraction, and selected actions...", "running");
  resetLiveLog(`Starting run for ${formatRunDate(runDate.value)}...`);

  try {
    const payload = {
      run_date: runDate.value,
      site_filters: sourcesForRun,
      upload_drive: uploadDrive.checked,
      save_webmail_draft: saveDraft.checked,
      send_email: sendEmail.checked,
    };
    const response = await fetch("/tasks/daily-run-stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!response.ok) {
      const text = await response.text();
      throw new Error(text || `Request failed with ${response.status}`);
    }
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    while (true) {
      const { value, done } = await reader.read();
      if (done) {
        break;
      }
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop() || "";
      for (const line of lines) {
        if (!line.trim()) {
          continue;
        }
        handleStreamEvent(JSON.parse(line));
      }
    }
    if (buffer.trim()) {
      handleStreamEvent(JSON.parse(buffer));
    }
  } catch (error) {
    resultsBody.innerHTML = '<tr><td colspan="4" class="empty">Run failed.</td></tr>';
    setMessage(error.message, "error");
    liveLogStatus.textContent = "Failed";
    liveDot?.classList.remove("live");
    appendLiveLog(error.message, "error");
  } finally {
    runButton.disabled = false;
  }
});

refreshSites.addEventListener("click", loadSites);
refreshHistory.addEventListener("click", loadHistory);
historyDate.addEventListener("change", loadHistory);
clearHistoryDate.addEventListener("click", () => {
  historyDate.value = "";
  loadHistory();
});
addSource.addEventListener("click", () => {
  const source = siteSelect.value;
  if (source && !selectedSources.includes(source)) {
    selectedSources.push(source);
    renderSelectedSources();
  }
});
clearSources.addEventListener("click", () => {
  selectedSources = [];
  renderSelectedSources();
});
selectedSourceList.addEventListener("click", (event) => {
  const button = event.target.closest(".remove-source");
  if (!button) {
    return;
  }
  selectedSources = selectedSources.filter((source) => source !== button.dataset.source);
  renderSelectedSources();
});
runDate.addEventListener("change", () => {
  if (dashboardDateChip) {
    dashboardDateChip.textContent = formatRunDate(runDate.value);
  }
});
sidebarToggle?.addEventListener("click", () => setSidebarHidden(!shell?.classList.contains("sidebar-collapsed")));

runDate.value = todayIso();
uploadDrive.checked = false;
if (dashboardDateChip) {
  dashboardDateChip.textContent = formatRunDate(runDate.value);
}
if (window.matchMedia("(max-width: 900px)").matches) {
  setSidebarHidden(true);
}
renderSelectedSources();
loadHealth();
loadSites();
loadHistory();
