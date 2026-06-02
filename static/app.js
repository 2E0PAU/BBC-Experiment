const streamGrid = document.querySelector("#streamGrid");
const statusText = document.querySelector("#statusText");
const statusDot = document.querySelector("#statusDot");
const refreshTime = document.querySelector("#refreshTime");
const refreshButton = document.querySelector("#refreshButton");
const template = document.querySelector("#streamCardTemplate");

const players = new Map();
let hasLoaded = false;

function setStatus(text, state) {
  statusText.textContent = text;
  statusDot.className = `status-dot ${state}`;
}

function formatDateTime(value) {
  if (!value) return "Unknown";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString([], {
    weekday: "short",
    hour: "2-digit",
    minute: "2-digit",
    day: "2-digit",
    month: "short",
  });
}

function updateIcons() {
  if (window.lucide) {
    window.lucide.createIcons();
  }
}

function destroyPlayers() {
  for (const player of players.values()) {
    player.destroy();
  }
  players.clear();
}

function showMessage(video, message, text) {
  message.textContent = text;
  video.hidden = true;
  const frame = video.closest(".player-frame");
  if (frame) frame.classList.add("no-video");
}

function attachPlayback(video, stream, message, attempt = 0) {
  const sources = (stream.playback || []).map((item) => item.url).filter(Boolean);

  if (!sources.length) {
    showMessage(video, message, "No playable live URL is available from BBC right now.");
    return;
  }
  if (attempt >= sources.length) {
    showMessage(video, message, "All BBC playback URLs failed for this camera.");
    return;
  }

  const source = sources[attempt];
  video.hidden = false;
  message.textContent = "";
  const frame = video.closest(".player-frame");
  if (frame) frame.classList.remove("no-video");

  if (window.Hls && window.Hls.isSupported()) {
    const hls = new window.Hls({
      liveSyncDurationCount: 4,
      enableWorker: true,
    });
    hls.on(window.Hls.Events.ERROR, (_event, data) => {
      if (!data.fatal) return;
      if (data.type === window.Hls.ErrorTypes.NETWORK_ERROR) {
        hls.startLoad();
      } else if (data.type === window.Hls.ErrorTypes.MEDIA_ERROR) {
        hls.recoverMediaError();
      } else {
        hls.destroy();
        players.delete(stream.vpid);
        if (attempt + 1 < sources.length) {
          // Fail over to the next priority URL BBC supplied.
          message.textContent = "Switching to a backup BBC stream...";
          attachPlayback(video, stream, message, attempt + 1);
        } else {
          message.textContent = `Playback error: ${data.type}`;
        }
      }
    });
    hls.loadSource(source);
    hls.attachMedia(video);
    players.set(stream.vpid, hls);
    return;
  }

  if (video.canPlayType("application/vnd.apple.mpegurl")) {
    video.src = source;
    return;
  }

  showMessage(video, message, "This browser cannot play HLS streams.");
}

function renderSkeletons(count = 4) {
  destroyPlayers();
  streamGrid.replaceChildren();
  for (let i = 0; i < count; i += 1) {
    const card = document.createElement("article");
    card.className = "stream-card skeleton-card";
    card.innerHTML = `
      <div class="player-frame skeleton-shimmer"></div>
      <div class="card-body">
        <div class="skeleton-line skeleton-shimmer" style="width:65%"></div>
        <div class="skeleton-line skeleton-shimmer"></div>
        <div class="skeleton-line skeleton-shimmer" style="width:80%"></div>
      </div>`;
    streamGrid.appendChild(card);
  }
}

function renderStreams(streams) {
  destroyPlayers();
  streamGrid.replaceChildren();

  if (!streams.length) {
    streamGrid.innerHTML = '<p class="empty-state">No Springwatch wildlife camera streams were found.</p>';
    return;
  }

  for (const stream of streams) {
    const card = template.content.firstElementChild.cloneNode(true);
    const video = card.querySelector(".stream-video");
    const poster = card.querySelector(".stream-poster");
    const message = card.querySelector(".player-message");
    const reload = card.querySelector(".reload-stream");
    const liveBadge = card.querySelector(".live-badge");

    card.querySelector(".stream-title").textContent = stream.title;
    card.querySelector(".stream-copy").textContent = stream.synopsis || "Live Springwatch wildlife camera feed.";
    card.querySelector(".stream-status").textContent = stream.status || "Unknown";
    liveBadge.hidden = (stream.status || "").toUpperCase() !== "LIVE";
    card.querySelector(".stream-window").textContent = `${formatDateTime(stream.schedule_start)} to ${formatDateTime(stream.schedule_end)}`;
    card.querySelector(".official-link").href = stream.official_url;

    if (stream.image_url) {
      poster.src = stream.image_url;
      poster.alt = stream.title;
    } else {
      poster.hidden = true;
    }

    attachPlayback(video, stream, message);
    reload.addEventListener("click", () => {
      video.pause();
      const oldPlayer = players.get(stream.vpid);
      if (oldPlayer) {
        oldPlayer.destroy();
        players.delete(stream.vpid);
      }
      attachPlayback(video, stream, message);
      video.load();
    });

    streamGrid.appendChild(card);
  }

  hasLoaded = true;
  updateIcons();
}

async function loadStreams() {
  setStatus("Refreshing", "loading");
  refreshButton.disabled = true;
  if (!hasLoaded) {
    renderSkeletons();
  }

  try {
    const response = await fetch("/api/streams", { cache: "no-store" });
    const payload = await response.json();
    if (!response.ok || !payload.ok) {
      throw new Error(payload.error || "Could not load BBC stream data.");
    }
    renderStreams(payload.streams);
    const count = payload.streams.length;
    setStatus(`${count} stream${count === 1 ? "" : "s"} discovered`, count ? "ok" : "error");
    refreshTime.textContent = new Date().toLocaleTimeString([], {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    });
  } catch (error) {
    setStatus("Error", "error");
    destroyPlayers();
    streamGrid.innerHTML = `<p class="empty-state">${error.message}</p>`;
  } finally {
    refreshButton.disabled = false;
    updateIcons();
  }
}

refreshButton.addEventListener("click", loadStreams);
window.addEventListener("DOMContentLoaded", loadStreams);
