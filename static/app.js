const streamGrid = document.querySelector("#streamGrid");
const statusText = document.querySelector("#statusText");
const refreshTime = document.querySelector("#refreshTime");
const refreshButton = document.querySelector("#refreshButton");
const template = document.querySelector("#streamCardTemplate");

const players = new Map();

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

function attachPlayback(video, stream, message) {
  const source = stream.playback?.[0]?.url;
  if (!source) {
    message.textContent = "No playable live URL is available from BBC right now.";
    video.hidden = true;
    return;
  }

  video.hidden = false;
  message.textContent = "";

  if (window.Hls && window.Hls.isSupported()) {
    const hls = new window.Hls({
      liveSyncDurationCount: 4,
      enableWorker: true,
    });
    hls.on(window.Hls.Events.ERROR, (_event, data) => {
      if (!data.fatal) return;
      message.textContent = `Playback error: ${data.type}`;
      if (data.type === window.Hls.ErrorTypes.NETWORK_ERROR) {
        hls.startLoad();
      } else if (data.type === window.Hls.ErrorTypes.MEDIA_ERROR) {
        hls.recoverMediaError();
      } else {
        hls.destroy();
        players.delete(stream.vpid);
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

  message.textContent = "This browser cannot play HLS streams.";
  video.hidden = true;
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

    card.querySelector(".stream-title").textContent = stream.title;
    card.querySelector(".stream-copy").textContent = stream.synopsis || "Live Springwatch wildlife camera feed.";
    card.querySelector(".stream-status").textContent = stream.status || "Unknown";
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

  updateIcons();
}

async function loadStreams() {
  statusText.textContent = "Refreshing";
  refreshButton.disabled = true;

  try {
    const response = await fetch("/api/streams", { cache: "no-store" });
    const payload = await response.json();
    if (!response.ok || !payload.ok) {
      throw new Error(payload.error || "Could not load BBC stream data.");
    }
    renderStreams(payload.streams);
    statusText.textContent = `${payload.streams.length} stream${payload.streams.length === 1 ? "" : "s"} discovered`;
    refreshTime.textContent = new Date().toLocaleTimeString([], {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    });
  } catch (error) {
    statusText.textContent = "Error";
    streamGrid.innerHTML = `<p class="empty-state">${error.message}</p>`;
  } finally {
    refreshButton.disabled = false;
    updateIcons();
  }
}

refreshButton.addEventListener("click", loadStreams);
window.addEventListener("DOMContentLoaded", loadStreams);
