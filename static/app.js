const jobId = window.JOB_ID;

const stepTranscribe = document.getElementById("step-transcribe");
const stepReview = document.getElementById("step-review");
const stepDone = document.getElementById("step-done");
const statusBadge = document.getElementById("status-badge");
const errorBox = document.getElementById("error-box");
const segmentsBody = document.getElementById("segments-body");

function showError(message) {
  errorBox.textContent = message;
  errorBox.style.display = "block";
}

function clearError() {
  errorBox.style.display = "none";
}

function setStatus(status) {
  statusBadge.textContent = status;
  statusBadge.className = "badge status-" + status;
}

function showStepFor(status) {
  stepTranscribe.style.display = "none";
  stepReview.style.display = "none";
  stepDone.style.display = "none";

  if (status === "uploaded" || status === "error") {
    stepTranscribe.style.display = "block";
  } else if (status === "transcribing") {
    stepTranscribe.style.display = "block";
  } else if (status === "review" || status === "rendering") {
    stepReview.style.display = "block";
    loadSegments();
  } else if (status === "done") {
    stepDone.style.display = "block";
    document.getElementById("download-link").href = `/jobs/${jobId}/download`;
  }
}

async function transcribe() {
  clearError();
  const btn = document.getElementById("btn-transcribe");
  btn.disabled = true;
  btn.textContent = "Transcrevendo...";
  try {
    const res = await fetch(`/jobs/${jobId}/transcribe`, { method: "POST" });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Falha na transcrição.");
    setStatus(data.status);
    showStepFor(data.status);
  } catch (e) {
    showError(e.message);
    setStatus("error");
  } finally {
    btn.disabled = false;
    btn.textContent = "Transcrever áudio";
  }
}

async function loadSegments() {
  const res = await fetch(`/jobs/${jobId}/segments`);
  if (!res.ok) return;
  const data = await res.json();
  renderSegmentsTable(data.segments);
}

function renderSegmentsTable(segments) {
  segmentsBody.innerHTML = "";
  for (const seg of segments) {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td><input type="number" class="f-verse" value="${seg.verse}" min="1"></td>
      <td><input type="number" step="0.01" class="f-start" value="${seg.start.toFixed(2)}"></td>
      <td><input type="number" step="0.01" class="f-end" value="${seg.end.toFixed(2)}"></td>
      <td><textarea class="f-text">${seg.text}</textarea></td>
    `;
    segmentsBody.appendChild(tr);
  }
}

function collectSegments() {
  const rows = segmentsBody.querySelectorAll("tr");
  const segments = [];
  rows.forEach((row) => {
    segments.push({
      verse: parseInt(row.querySelector(".f-verse").value, 10),
      start: parseFloat(row.querySelector(".f-start").value),
      end: parseFloat(row.querySelector(".f-end").value),
      text: row.querySelector(".f-text").value.trim(),
    });
  });
  return segments;
}

async function saveSegments() {
  clearError();
  const btn = document.getElementById("btn-save-segments");
  btn.disabled = true;
  try {
    const res = await fetch(`/jobs/${jobId}/segments`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ segments: collectSegments() }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Falha ao salvar.");
  } catch (e) {
    showError(e.message);
  } finally {
    btn.disabled = false;
  }
}

async function renderVideo() {
  clearError();
  await saveSegments();
  const btn = document.getElementById("btn-render");
  btn.disabled = true;
  btn.textContent = "Renderizando... isso pode levar alguns minutos";
  try {
    const res = await fetch(`/jobs/${jobId}/render`, { method: "POST" });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Falha ao renderizar.");
    setStatus(data.status);
    showStepFor(data.status);
  } catch (e) {
    showError(e.message);
    setStatus("error");
  } finally {
    btn.disabled = false;
    btn.textContent = "Renderizar vídeo";
  }
}

document.getElementById("btn-transcribe").addEventListener("click", transcribe);
document.getElementById("btn-save-segments").addEventListener("click", saveSegments);
document.getElementById("btn-render").addEventListener("click", renderVideo);

showStepFor(window.INITIAL_STATUS);
