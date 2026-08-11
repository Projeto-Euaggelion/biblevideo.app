// Este arquivo é carregado em todas as páginas (via base.html), então cada
// bloco de funcionalidade só é ativado se os elementos correspondentes
// existirem na página atual.

const jobId = window.JOB_ID;

const stepTranscribe = document.getElementById("step-transcribe");
const stepReview = document.getElementById("step-review");
const stepDone = document.getElementById("step-done");
const statusBadge = document.getElementById("status-badge");
const errorBox = document.getElementById("error-box");
const videoPreview = document.getElementById("video-preview");
const downloadLink = document.getElementById("download-link");

// Editor de revisão (waveform + texto sincronizado)
const audioPlayer = document.getElementById("audio-player");
const btnPlay = document.getElementById("btn-play");
const timeDisplay = document.getElementById("time-display");
const waveformCanvas = document.getElementById("waveform-canvas");
const waveformContainer = document.getElementById("waveform-container");
const waveformOverlay = document.getElementById("waveform-overlay");
const playhead = document.getElementById("playhead");
const segmentsList = document.getElementById("segments-list");

let currentSegments = [];
let audioBuffer = null;
let audioLoadStarted = false;

function showError(message) {
  if (!errorBox) return;
  errorBox.textContent = message;
  errorBox.style.display = "block";
}

function clearError() {
  if (!errorBox) return;
  errorBox.style.display = "none";
}

function setStatus(status) {
  if (!statusBadge) return;
  statusBadge.textContent = status;
  statusBadge.className = "badge status-" + status;
}

function showStepFor(status) {
  if (!stepTranscribe || !stepReview || !stepDone) return;

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
    if (downloadLink) downloadLink.href = `/jobs/${jobId}/download`;
    if (videoPreview && !videoPreview.src) {
      videoPreview.src = `/jobs/${jobId}/video`;
    }
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
  currentSegments = data.segments.map((s) => ({
    verse: s.verse,
    start: s.start,
    end: s.end,
    text: s.text,
  }));
  renderSegmentsList();
  initAudio();
}

// ---- Player de áudio + waveform ----

function initAudio() {
  if (audioLoadStarted || !audioPlayer) return;
  audioLoadStarted = true;
  audioPlayer.src = `/jobs/${jobId}/audio`;
  loadWaveform();
}

function getDuration() {
  if (audioPlayer && !isNaN(audioPlayer.duration) && audioPlayer.duration > 0) {
    return audioPlayer.duration;
  }
  if (audioBuffer) return audioBuffer.duration;
  return 0;
}

function formatTime(seconds) {
  if (!isFinite(seconds) || seconds < 0) seconds = 0;
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

async function loadWaveform() {
  if (!waveformCanvas) return;
  try {
    const res = await fetch(`/jobs/${jobId}/audio`);
    const arrayBuffer = await res.arrayBuffer();
    const AudioCtx = window.AudioContext || window.webkitAudioContext;
    const audioCtx = new AudioCtx();
    audioBuffer = await audioCtx.decodeAudioData(arrayBuffer);
    drawWaveform();
    renderRegions();
  } catch (e) {
    // Sem waveform desenhada, mas o player e a edição de tempos continuam
    // funcionando normalmente (dependem só dos metadados do <audio>).
    console.error("Não foi possível gerar a forma de onda:", e);
  }
}

function drawWaveform() {
  if (!audioBuffer || !waveformCanvas || !waveformContainer) return;
  const dpr = window.devicePixelRatio || 1;
  const width = waveformContainer.clientWidth;
  const height = waveformContainer.clientHeight;
  if (width === 0 || height === 0) return;

  waveformCanvas.width = width * dpr;
  waveformCanvas.height = height * dpr;
  const ctx = waveformCanvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, width, height);

  const data = audioBuffer.getChannelData(0);
  const samplesPerPixel = Math.max(1, Math.floor(data.length / width));
  const mid = height / 2;
  ctx.fillStyle = "#71717a";

  for (let x = 0; x < width; x++) {
    const start = x * samplesPerPixel;
    const end = Math.min(start + samplesPerPixel, data.length);
    let min = 0;
    let max = 0;
    for (let i = start; i < end; i++) {
      const v = data[i];
      if (v < min) min = v;
      if (v > max) max = v;
    }
    const y1 = mid + min * mid * 0.9;
    const y2 = mid + max * mid * 0.9;
    ctx.fillRect(x, y1, 1, Math.max(1, y2 - y1));
  }
}

function renderRegions() {
  if (!waveformOverlay) return;
  const duration = getDuration();
  waveformOverlay.querySelectorAll(".region, .handle").forEach((el) => el.remove());
  if (!duration) return;

  currentSegments.forEach((seg, idx) => {
    const left = (seg.start / duration) * 100;
    const width = Math.max(0, ((seg.end - seg.start) / duration) * 100);

    const region = document.createElement("div");
    region.className = "region " + (idx % 2 === 0 ? "region-even" : "region-odd");
    region.dataset.index = String(idx);
    region.style.left = left + "%";
    region.style.width = width + "%";
    const label = document.createElement("span");
    label.className = "region-label";
    label.textContent = seg.verse;
    region.appendChild(label);
    waveformOverlay.appendChild(region);

    const startHandle = document.createElement("div");
    startHandle.className = "handle handle-start";
    startHandle.dataset.index = String(idx);
    startHandle.dataset.edge = "start";
    startHandle.style.left = left + "%";
    waveformOverlay.appendChild(startHandle);

    const endHandle = document.createElement("div");
    endHandle.className = "handle handle-end";
    endHandle.dataset.index = String(idx);
    endHandle.dataset.edge = "end";
    endHandle.style.left = left + width + "%";
    waveformOverlay.appendChild(endHandle);
  });

  if (playhead) waveformOverlay.appendChild(playhead);
}

function updateRegionPosition(idx) {
  const duration = getDuration();
  if (!duration || !waveformOverlay) return;
  const seg = currentSegments[idx];
  const left = (seg.start / duration) * 100;
  const width = Math.max(0, ((seg.end - seg.start) / duration) * 100);

  const region = waveformOverlay.querySelector(`.region[data-index="${idx}"]`);
  const hs = waveformOverlay.querySelector(`.handle-start[data-index="${idx}"]`);
  const he = waveformOverlay.querySelector(`.handle-end[data-index="${idx}"]`);
  if (region) {
    region.style.left = left + "%";
    region.style.width = width + "%";
  }
  if (hs) hs.style.left = left + "%";
  if (he) he.style.left = left + width + "%";
}

function updateSegmentCardFields(idx) {
  if (!segmentsList) return;
  const card = segmentsList.querySelector(`.segment-card[data-index="${idx}"]`);
  if (!card) return;
  const seg = currentSegments[idx];
  const startInput = card.querySelector(".f-start");
  const endInput = card.querySelector(".f-end");
  if (startInput && document.activeElement !== startInput) startInput.value = seg.start.toFixed(2);
  if (endInput && document.activeElement !== endInput) endInput.value = seg.end.toFixed(2);
}

function seekTo(time) {
  if (!audioPlayer) return;
  const duration = getDuration();
  audioPlayer.currentTime = Math.min(Math.max(time, 0), duration || time);
}

function updatePlayheadUI() {
  const duration = getDuration();
  if (playhead && duration) {
    const pct = (audioPlayer.currentTime / duration) * 100;
    playhead.style.left = pct + "%";
  }
  if (timeDisplay) {
    timeDisplay.textContent = `${formatTime(audioPlayer.currentTime)} / ${formatTime(duration)}`;
  }
  highlightPlayingSegment();
}

function highlightPlayingSegment() {
  if (!segmentsList) return;
  const t = audioPlayer.currentTime;
  const idx = currentSegments.findIndex((s) => t >= s.start && t < s.end);
  segmentsList.querySelectorAll(".segment-card").forEach((card) => {
    const isActive = parseInt(card.dataset.index, 10) === idx;
    card.classList.toggle("is-playing", isActive);
    if (isActive) card.scrollIntoView({ block: "nearest", behavior: "smooth" });
  });
}

function renderSegmentsList() {
  if (!segmentsList) return;
  segmentsList.innerHTML = "";
  currentSegments.forEach((seg, idx) => {
    const card = document.createElement("div");
    card.className = "segment-card";
    card.dataset.index = String(idx);
    card.innerHTML = `
      <label class="segment-field">
        <span>Vers.</span>
        <input type="number" class="f-verse" value="${seg.verse}" min="1">
      </label>
      <label class="segment-field">
        <span>Início (s)</span>
        <input type="number" step="0.01" class="f-start" value="${seg.start.toFixed(2)}">
      </label>
      <label class="segment-field">
        <span>Fim (s)</span>
        <input type="number" step="0.01" class="f-end" value="${seg.end.toFixed(2)}">
      </label>
      <button type="button" class="segment-play-btn" title="Ouvir este trecho">▶</button>
      <textarea class="f-text">${seg.text}</textarea>
    `;
    segmentsList.appendChild(card);

    card.querySelector(".f-verse").addEventListener("input", (e) => {
      currentSegments[idx].verse = parseInt(e.target.value, 10) || seg.verse;
    });
    card.querySelector(".f-start").addEventListener("input", (e) => {
      const v = parseFloat(e.target.value);
      if (!isNaN(v)) {
        currentSegments[idx].start = v;
        updateRegionPosition(idx);
      }
    });
    card.querySelector(".f-end").addEventListener("input", (e) => {
      const v = parseFloat(e.target.value);
      if (!isNaN(v)) {
        currentSegments[idx].end = v;
        updateRegionPosition(idx);
      }
    });
    card.querySelector(".f-text").addEventListener("input", (e) => {
      currentSegments[idx].text = e.target.value;
    });
    card.querySelector(".segment-play-btn").addEventListener("click", () => {
      seekTo(currentSegments[idx].start);
      audioPlayer.play();
    });
  });
}

function collectSegments() {
  return currentSegments.map((s) => ({
    verse: s.verse,
    start: s.start,
    end: s.end,
    text: (s.text || "").trim(),
  }));
}

// Clique na faixa para buscar posição; arraste nas bordas para ajustar tempos
if (waveformOverlay) {
  waveformOverlay.addEventListener("pointerdown", (e) => {
    const target = e.target;
    if (target.classList && target.classList.contains("handle")) {
      e.preventDefault();
      target.classList.add("is-dragging");
      const idx = parseInt(target.dataset.index, 10);
      const edge = target.dataset.edge;
      const rect = waveformOverlay.getBoundingClientRect();
      const duration = getDuration();
      const MIN_GAP = 0.05;

      const onMove = (ev) => {
        const x = Math.min(Math.max(ev.clientX - rect.left, 0), rect.width);
        const t = duration ? (x / rect.width) * duration : 0;
        const seg = currentSegments[idx];
        if (edge === "start") {
          seg.start = Math.max(0, Math.min(t, seg.end - MIN_GAP));
        } else {
          seg.end = Math.min(duration || t, Math.max(t, seg.start + MIN_GAP));
        }
        updateRegionPosition(idx);
        updateSegmentCardFields(idx);
      };
      const onUp = () => {
        target.classList.remove("is-dragging");
        document.removeEventListener("pointermove", onMove);
        document.removeEventListener("pointerup", onUp);
      };
      document.addEventListener("pointermove", onMove);
      document.addEventListener("pointerup", onUp);
    } else {
      const rect = waveformOverlay.getBoundingClientRect();
      const x = Math.min(Math.max(e.clientX - rect.left, 0), rect.width);
      const duration = getDuration();
      if (duration) seekTo((x / rect.width) * duration);
    }
  });
}

if (audioPlayer) {
  audioPlayer.addEventListener("timeupdate", updatePlayheadUI);
  audioPlayer.addEventListener("loadedmetadata", () => {
    renderRegions();
    updatePlayheadUI();
  });
  audioPlayer.addEventListener("play", () => {
    if (btnPlay) btnPlay.textContent = "⏸";
  });
  audioPlayer.addEventListener("pause", () => {
    if (btnPlay) btnPlay.textContent = "▶";
  });
}

if (btnPlay) {
  btnPlay.addEventListener("click", () => {
    if (!audioPlayer) return;
    if (audioPlayer.paused) audioPlayer.play();
    else audioPlayer.pause();
  });
}

document.addEventListener("keydown", (e) => {
  if (!audioPlayer || !stepReview || stepReview.style.display === "none") return;
  const tag = document.activeElement ? document.activeElement.tagName : "";
  if (e.code === "Space" && tag !== "INPUT" && tag !== "TEXTAREA") {
    e.preventDefault();
    if (audioPlayer.paused) audioPlayer.play();
    else audioPlayer.pause();
  }
});

function debounce(fn, wait) {
  let t;
  return (...args) => {
    clearTimeout(t);
    t = setTimeout(() => fn(...args), wait);
  };
}

window.addEventListener(
  "resize",
  debounce(() => {
    drawWaveform();
    renderRegions();
  }, 150)
);

// ---- Salvar / renderizar ----

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

const btnTranscribe = document.getElementById("btn-transcribe");
const btnSaveSegments = document.getElementById("btn-save-segments");
const btnRender = document.getElementById("btn-render");

if (btnTranscribe) btnTranscribe.addEventListener("click", transcribe);
if (btnSaveSegments) btnSaveSegments.addEventListener("click", saveSegments);
if (btnRender) btnRender.addEventListener("click", renderVideo);

if (jobId && window.INITIAL_STATUS) {
  showStepFor(window.INITIAL_STATUS);
}