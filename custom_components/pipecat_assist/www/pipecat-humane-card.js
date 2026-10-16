/**
 * Pipecat Humane Card - A warm, simplistic, human-centered conversational card
 * with high legibility, clean chat bubbles, streaming text transitions, and theme harmony.
 */

const PIPECAT_HUMANE_CARD_VERSION = "1.0.0";
const DEFAULT_ACCENT_HEX = "#4f46e5";
const DEFAULT_AUDIO_BUFFER_MS = 120;
const HA_ASSIST_SAMPLE_RATE_FALLBACK = 48000;

const OPUS_AUDIO_QUALITY_PARAMS = {
  minptime: "20",
  useinbandfec: "1",
  maxplaybackrate: "48000",
  maxaveragebitrate: "96000",
  usedtx: "0",
};
const OPUS_AUDIO_REMOVE_PARAMS = new Set(["stereo", "sprop-stereo"]);

const HUMANE_TRANSLATIONS = {
  en: {
    greeting: "How can I help you today?",
    talk: "Talk",
    stop: "Stop",
    listening: "Listening...",
    thinking: "Thinking...",
    speaking: "Speaking...",
    ready: "Ready",
    connecting: "Connecting...",
    audioBlocked: "Audio playback blocked. Tap to enable.",
    microphoneUnavailable: "Microphone access is unavailable.",
    microphoneBlocked: "Microphone blocked. Allow access and retry.",
  },
  pl: {
    greeting: "W czym mogę Ci dziś pomóc?",
    talk: "Rozmawiaj",
    stop: "Zakończ",
    listening: "Słucham...",
    thinking: "Myślę...",
    speaking: "Mówię...",
    ready: "Gotowy",
    connecting: "Łączenie...",
    audioBlocked: "Dźwięk zablokowany. Dotknij, aby włączyć.",
    microphoneUnavailable: "Brak dostępu do mikrofonu.",
    microphoneBlocked: "Mikrofon zablokowany. Zezwól na dostęp.",
  },
};

function languageBase(value) {
  return String(value || "").toLowerCase().split(/[-_]/)[0] || "en";
}

function escapeHtml(value) {
  return String(value || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

function normalizeHexColor(value, fallback = DEFAULT_ACCENT_HEX) {
  const raw = String(value || "").trim();
  const short = /^#?([0-9a-f]{3})$/i.exec(raw);
  if (short) {
    const [r, g, b] = short[1].split("");
    return `#${r}${r}${g}${g}${b}${b}`.toLowerCase();
  }
  const full = /^#?([0-9a-f]{6})$/i.exec(raw);
  return full ? `#${full[1].toLowerCase()}` : fallback;
}

function hexToRgb(value) {
  const hex = normalizeHexColor(value).slice(1);
  return {
    r: Number.parseInt(hex.slice(0, 2), 16),
    g: Number.parseInt(hex.slice(2, 4), 16),
    b: Number.parseInt(hex.slice(4, 6), 16),
  };
}

function normalizeTranscriptText(value) {
  return String(value || "")
    .replace(/\u00a0/g, " ")
    .replace(/\s+/g, " ")
    .replace(/\s+([,.;:!?%…)\]}])/g, "$1")
    .replace(/([,.;:!?])(?=\p{L}|\p{N})/gu, "$1 ")
    .replace(/([([{])\s+/g, "$1")
    .trim();
}

function mergeOpusFmtp(existing) {
  const params = new Map();
  for (const part of existing.split(";").map((item) => item.trim()).filter(Boolean)) {
    const [rawKey, ...rest] = part.split("=");
    const key = rawKey.trim().toLowerCase();
    if (!key || OPUS_AUDIO_REMOVE_PARAMS.has(key)) continue;
    params.set(key, rest.length ? rest.join("=").trim() : "");
  }
  for (const [key, value] of Object.entries(OPUS_AUDIO_QUALITY_PARAMS)) params.set(key, value);
  return [...params.entries()].map(([key, value]) => (value ? `${key}=${value}` : key)).join(";");
}

function preferFullbandOpus(sdp) {
  if (!sdp) return sdp;
  const separator = sdp.includes("\r\n") ? "\r\n" : "\n";
  const lines = sdp.split(/\r?\n/);
  const opusPayloads = new Set();
  const fmtpPayloads = new Set();

  for (const line of lines) {
    const rtpmap = /^a=rtpmap:(\d+)\s+opus\/48000(?:\/2)?/i.exec(line);
    if (rtpmap) opusPayloads.add(rtpmap[1]);
    const fmtp = /^a=fmtp:(\d+)\s+/i.exec(line);
    if (fmtp) fmtpPayloads.add(fmtp[1]);
  }

  return lines.map((line) => {
    const fmtp = /^a=fmtp:(\d+)\s*(.*)$/i.exec(line);
    if (fmtp && opusPayloads.has(fmtp[1])) {
      return `a=fmtp:${fmtp[1]} ${mergeOpusFmtp(fmtp[2] || "")}`;
    }
    const rtpmap = /^a=rtpmap:(\d+)\s+opus\/48000(?:\/2)?/i.exec(line);
    if (rtpmap && !fmtpPayloads.has(rtpmap[1])) {
      return `${line}${separator}a=fmtp:${rtpmap[1]} ${mergeOpusFmtp("")}`;
    }
    return line;
  }).join(separator);
}

class PipecatHumaneCard extends HTMLElement {
  constructor() {
    super();
    this.state = "idle";
    this.detail = "";
    this.messages = []; // { id, role: 'user' | 'assistant', text }
    this.messageSeq = 0;
    this.audioBlocked = false;
    this.visualizerEnergy = 0;
    this.stopOnPageExit = () => this.stop();
  }

  static getStubConfig() {
    return { name: "Pipecat Companion" };
  }

  connectedCallback() {
    window.addEventListener("pagehide", this.stopOnPageExit);
    window.addEventListener("beforeunload", this.stopOnPageExit);
    if (this.shadowRoot && !this.visualizerFrame) {
      this.ensureVisualizer();
    }
  }

  disconnectedCallback() {
    window.removeEventListener("pagehide", this.stopOnPageExit);
    window.removeEventListener("beforeunload", this.stopOnPageExit);
    this.stop();
  }

  setConfig(config) {
    this.config = config || {};
    if (!this.shadowRoot) this.attachShadow({ mode: "open" });
    this.state = "idle";
    this.render();
  }

  set hass(value) {
    const prevLang = this.uiLanguage();
    this._hass = value;
    if (prevLang !== this.uiLanguage() && this.shadowRoot) this.render();
  }

  getCardSize() {
    return 3;
  }

  uiLanguage() {
    return languageBase(
      this._hass?.language
        || this._hass?.locale?.language
        || this.config?.language
        || navigator.language
    );
  }

  t(key) {
    const lang = this.uiLanguage();
    return HUMANE_TRANSLATIONS[lang]?.[key] || HUMANE_TRANSLATIONS.en[key] || key;
  }

  accentHex() {
    return normalizeHexColor(this.config.accent_color || this.config.color);
  }

  accentRgb() {
    return hexToRgb(this.accentHex());
  }

  baseUrl() {
    return (this.config.url || "").replace(/\/$/, "");
  }

  proxyMode() {
    return !this.baseUrl();
  }

  apiUrl(path) {
    const base = this.baseUrl();
    if (!base) return path;
    return `${base}/${path.replace(/^\//, "")}`;
  }

  authHeaders() {
    if (!this.proxyMode()) return {};
    const token = this._hass?.auth?.data?.access_token
      || this._hass?.connection?.options?.auth?.data?.access_token;
    return token ? { Authorization: `Bearer ${token}` } : {};
  }

  entryQuery() {
    return this.config.entry_id ? `?entry_id=${encodeURIComponent(this.config.entry_id)}` : "";
  }

  clientId() {
    const key = "pipecat-humane-card-client-id";
    let id = localStorage.getItem(key);
    if (!id) {
      id = crypto.randomUUID();
      localStorage.setItem(key, id);
    }
    return id;
  }

  sessionLanguage() {
    return this.config.language
      || this._hass?.language
      || this._hass?.locale?.language
      || navigator.language
      || "en";
  }

  statusLabel() {
    if (this.state === "error") return this.detail || this.t("ready");
    if (this.state === "connecting") return this.t("connecting");
    if (this.state === "connected") return this.t("listening");
    return this.t("ready");
  }

  async loadAddonConfig() {
    const path = this.proxyMode()
      ? `/api/pipecat_assist/config${this.entryQuery()}`
      : "/api/assist/config";
    const res = await fetch(this.apiUrl(path), { headers: this.authHeaders() });
    if (!res.ok) throw new Error(`Config failed HTTP ${res.status}`);
    return res.json();
  }

  async offerErrorMessage(response) {
    const body = await response.text();
    try {
      const parsed = JSON.parse(body);
      return parsed.detail || parsed.error || body || `Offer failed ${response.status}`;
    } catch {
      return body || `Offer failed ${response.status}`;
    }
  }

  async waitForIce(pc, timeoutMs = 2500) {
    if (pc.iceGatheringState === "complete") return;
    await new Promise((resolve) => {
      let timer;
      const done = () => {
        clearTimeout(timer);
        pc.removeEventListener("icegatheringstatechange", onChange);
        resolve();
      };
      const onChange = () => {
        if (pc.iceGatheringState === "complete") done();
      };
      timer = setTimeout(done, timeoutMs);
      pc.addEventListener("icegatheringstatechange", onChange);
    });
  }

  resetAudioElement() {
    if (!this.audio) return;
    this.audio.pause();
    this.audio.srcObject = null;
    this.audio.removeAttribute("src");
    try { this.audio.load(); } catch {}
  }

  attachAudio() {
    if (!this.audio || !this.remoteStream) return;
    if (this.audio.srcObject !== this.remoteStream) this.audio.srcObject = this.remoteStream;
    this.audio.autoplay = true;
    this.audio.playsInline = true;
    this.audio.muted = false;
    this.audio.volume = 1;
    const playPromise = this.audio.play();
    if (playPromise?.catch) {
      playPromise.catch((err) => {
        if (err?.name === "NotAllowedError" && !this.audioBlocked) {
          this.audioBlocked = true;
          this.render();
        }
      });
    }
  }

  appendMessage(role, text) {
    const id = `msg-${++this.messageSeq}`;
    this.messages.push({ id, role, text: normalizeTranscriptText(text) });
    if (this.messages.length > 20) this.messages.shift();
    this.renderTranscript();
    return id;
  }

  updateMessage(id, text) {
    const msg = this.messages.find((m) => m.id === id);
    if (!msg) return;
    msg.text = normalizeTranscriptText(text);
    const bubble = this.shadowRoot?.querySelector(`[data-msg-id="${id}"]`);
    if (bubble) {
      bubble.textContent = msg.text;
      this.scrollToBottom();
    } else {
      this.renderTranscript();
    }
  }

  renderTranscript() {
    const list = this.shadowRoot?.querySelector(".message-list");
    if (!list) return;

    if (!this.messages.length) {
      list.innerHTML = `<div class="empty-state">${escapeHtml(this.t("greeting"))}</div>`;
      return;
    }

    list.innerHTML = this.messages
      .map(
        (m) => `
        <div class="msg-bubble ${m.role}" data-msg-id="${m.id}">
          ${escapeHtml(m.text)}
        </div>
      `
      )
      .join("");
    this.scrollToBottom();
  }

  scrollToBottom() {
    const scrollContainer = this.shadowRoot?.querySelector(".transcript-container");
    if (scrollContainer) {
      scrollContainer.scrollTop = scrollContainer.scrollHeight;
    }
  }

  handleRealtimeMessage(raw) {
    if (typeof raw !== "string" || !raw.trim().startsWith("{")) return;
    let msg;
    try { msg = JSON.parse(raw); } catch { return; }
    const type = String(msg.type || msg.event || msg.name || "").toLowerCase();

    if (["conversation-ended", "conversation_end", "end-conversation"].includes(type)) {
      this.stop();
      return;
    }

    // Extract text
    const text = msg.text || msg.data?.text || msg.transcript || msg.data?.transcript || "";
    if (!text) return;

    if (type.includes("user")) {
      if (!this.currentUserMsgId) {
        this.currentUserMsgId = this.appendMessage("user", text);
      } else {
        this.updateMessage(this.currentUserMsgId, text);
      }
      if (type.includes("final")) this.currentUserMsgId = null;
    } else if (type.includes("bot") || type.includes("assistant")) {
      this.currentUserMsgId = null;
      if (!this.currentBotMsgId) {
        this.currentBotMsgId = this.appendMessage("assistant", text);
      } else {
        this.updateMessage(this.currentBotMsgId, text);
      }
      if (type.includes("final") || type.includes("stopped")) this.currentBotMsgId = null;
    }
  }

  async start() {
    if (!navigator.mediaDevices?.getUserMedia) {
      this.state = "error";
      this.detail = this.t("microphoneUnavailable");
      this.render();
      return;
    }

    try {
      this.state = "connecting";
      this.detail = this.t("connecting");
      this.audioBlocked = false;
      this.render();
      this.resetAudioElement();

      const addonConfig = await this.loadAddonConfig();
      this.stream = await navigator.mediaDevices.getUserMedia({
        audio: { autoGainControl: true, echoCancellation: true, noiseSuppression: true },
        video: false,
      });

      this.ensureVisualizer();

      const peer = new RTCPeerConnection();
      this.peer = peer;
      const track = this.stream.getAudioTracks()[0];
      if (track) peer.addTransceiver(track, { direction: "sendrecv" });
      else peer.addTransceiver("audio", { direction: "sendrecv" });

      this.channel = peer.createDataChannel("signalling");
      this.channel.onmessage = (e) => this.handleRealtimeMessage(e.data);
      this.channel.onopen = () => {
        this.channel.send(JSON.stringify({
          label: "rtvi-ai",
          id: crypto.randomUUID().slice(0, 8),
          type: "client-ready",
          data: {
            version: "1.4.0",
            about: {
              library: "pipecat-humane-card",
              library_version: PIPECAT_HUMANE_CARD_VERSION,
              platform: "home-assistant",
            },
          },
        }));
      };

      peer.ontrack = (event) => {
        if (event.track.kind !== "audio") return;
        this.remoteStream = event.streams[0] || new MediaStream([event.track]);
        this.ensureVisualizer();
        this.attachAudio();
      };

      peer.onconnectionstatechange = () => {
        if (peer.connectionState === "connected") {
          this.state = "connected";
          this.render();
        }
        if (["failed", "disconnected"].includes(peer.connectionState)) {
          this.state = "error";
          this.detail = `WebRTC ${peer.connectionState}`;
          this.render();
        }
      };

      const offer = await peer.createOffer({ voiceActivityDetection: false });
      await peer.setLocalDescription({ type: offer.type, sdp: preferFullbandOpus(offer.sdp) });
      await this.waitForIce(peer);

      const offerPath = this.proxyMode()
        ? `/api/pipecat_assist/offer${this.entryQuery()}`
        : addonConfig.runner_offer_path || "api/offer";

      const requestData = {
        source: "humane_card",
        client_id: this.clientId(),
        language: this.sessionLanguage(),
      };
      if (this.config.flow_id) requestData.flow_id = this.config.flow_id;

      const res = await fetch(this.apiUrl(offerPath), {
        method: "POST",
        headers: { "Content-Type": "application/json", ...this.authHeaders() },
        body: JSON.stringify({
          sdp: peer.localDescription.sdp,
          type: peer.localDescription.type,
          request_data: requestData,
        }),
      });

      if (!res.ok) throw new Error(await this.offerErrorMessage(res));
      const answer = await res.json();
      await peer.setRemoteDescription({ sdp: answer.sdp, type: answer.type });
      this.attachAudio();
    } catch (err) {
      this.state = "error";
      this.detail = err?.name === "NotAllowedError" ? this.t("microphoneBlocked") : (err?.message || "Failed to start");
      this.stop();
      this.render();
    }
  }

  stop() {
    if (this.channel?.readyState === "open") {
      try {
        this.channel.send(JSON.stringify({
          label: "rtvi-ai",
          id: crypto.randomUUID().slice(0, 8),
          type: "disconnect-bot",
          data: {},
        }));
      } catch {}
    }
    this.channel?.close();
    this.channel = undefined;
    this.peer?.getSenders?.().forEach((s) => s.track?.stop());
    this.peer?.getReceivers?.().forEach((r) => r.track?.stop());
    this.peer?.close();
    this.peer = undefined;
    this.stream?.getTracks().forEach((t) => t.stop());
    this.stream = undefined;
    this.remoteStream?.getTracks().forEach((t) => t.stop());
    this.remoteStream = undefined;
    this.resetAudioElement();
    this.stopVisualizer();
    this.currentUserMsgId = null;
    this.currentBotMsgId = null;
    this.state = "idle";
    this.render();
  }

  ensureVisualizerInput(name, stream) {
    if (!stream?.getAudioTracks?.().length) return;
    const AudioContextConstructor = window.AudioContext || window.webkitAudioContext;
    if (!AudioContextConstructor) return;
    try {
      if (!this.visualizerContext || this.visualizerContext.state === "closed") {
        this.visualizerContext = new AudioContextConstructor();
      }
      if (this.visualizerContext.state === "suspended") {
        this.visualizerContext.resume().catch(() => {});
      }
      const source = this.visualizerContext.createMediaStreamSource(stream);
      const analyser = this.visualizerContext.createAnalyser();
      analyser.fftSize = 128;
      analyser.smoothingTimeConstant = 0.8;
      source.connect(analyser);
      this.visualizerInputs = this.visualizerInputs || {};
      this.visualizerInputs[name] = {
        analyser,
        data: new Uint8Array(analyser.frequencyBinCount),
        source,
      };
    } catch {}
  }

  ensureVisualizer() {
    this.visualizerCanvas = this.shadowRoot?.querySelector("canvas.mini-orb");
    if (this.stream) this.ensureVisualizerInput("local", this.stream);
    if (this.remoteStream) this.ensureVisualizerInput("remote", this.remoteStream);
    if (!this.visualizerFrame) this.drawVisualizer();
  }

  stopVisualizer() {
    if (this.visualizerFrame) {
      cancelAnimationFrame(this.visualizerFrame);
      this.visualizerFrame = undefined;
    }
    if (this.visualizerInputs) {
      Object.values(this.visualizerInputs).forEach((i) => {
        try { i.source.disconnect(); } catch {}
      });
      this.visualizerInputs = {};
    }
    if (this.visualizerContext && this.visualizerContext.state !== "closed") {
      this.visualizerContext.close().catch(() => {});
    }
    this.visualizerContext = undefined;
    this.visualizerEnergy = 0;
  }

  visualizerEnergyFor(name) {
    const input = this.visualizerInputs?.[name];
    if (!input?.analyser) return 0;
    input.analyser.getByteFrequencyData(input.data);
    let sum = 0;
    const len = Math.min(input.data.length, 32);
    for (let i = 0; i < len; i++) sum += input.data[i];
    return Math.min(1, sum / len / 140);
  }

  drawVisualizer() {
    const canvas = this.visualizerCanvas;
    if (canvas) {
      const rect = canvas.getBoundingClientRect();
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      const w = Math.max(1, Math.floor(rect.width * dpr));
      const h = Math.max(1, Math.floor(rect.height * dpr));
      if (canvas.width !== w || canvas.height !== h) {
        canvas.width = w;
        canvas.height = h;
      }

      const ctx = canvas.getContext("2d");
      const accent = this.accentRgb();
      const localE = this.visualizerEnergyFor("local");
      const remoteE = this.visualizerEnergyFor("remote");
      const running = this.state === "connected";
      const idleE = this.state === "connecting" ? 0.2 : 0.06;
      const targetE = Math.max(localE, remoteE, idleE);
      this.visualizerEnergy = (this.visualizerEnergy || 0) * 0.8 + targetE * 0.2;
      const energy = this.visualizerEnergy;
      const time = performance.now() / 1000;

      ctx.clearRect(0, 0, w, h);
      const cx = w / 2;
      const cy = h / 2;
      const r = (Math.min(w, h) / 2) * (0.68 + energy * 0.25);

      const grad = ctx.createRadialGradient(cx, cy, 0, cx, cy, r);
      grad.addColorStop(0, "#ffffff");
      grad.addColorStop(0.4, `rgba(${accent.r}, ${accent.g}, ${accent.b}, 0.9)`);
      grad.addColorStop(1, `rgba(${Math.max(0, accent.r - 30)}, ${Math.max(0, accent.g - 30)}, ${accent.b}, 0)`);

      ctx.fillStyle = grad;
      ctx.beginPath();
      ctx.arc(cx, cy, r, 0, Math.PI * 2);
      ctx.fill();
    }
    this.visualizerFrame = requestAnimationFrame(() => this.drawVisualizer());
  }

  render() {
    if (!this.shadowRoot) return;
    const running = ["connecting", "connected"].includes(this.state);
    const accentHex = this.accentHex();
    const accent = this.accentRgb();
    const status = this.statusLabel();

    this.shadowRoot.innerHTML = `
      <style>
        ha-card {
          display: flex;
          flex-direction: column;
          height: 380px;
          border-radius: 24px;
          background: var(--ha-card-background, var(--card-background-color, #ffffff));
          color: var(--primary-text-color, #1f2937);
          border: 1px solid var(--divider-color, rgba(127, 127, 127, 0.12));
          box-shadow: 0 10px 30px rgba(0, 0, 0, 0.06);
          overflow: hidden;
          box-sizing: border-box;
          font-family: var(--paper-font-body1_-_font-family, inherit);
        }
        .header {
          display: flex;
          align-items: center;
          justify-content: space-between;
          padding: 16px 20px;
          border-bottom: 1px solid var(--divider-color, rgba(127, 127, 127, 0.08));
        }
        .title-group {
          display: flex;
          align-items: center;
          gap: 12px;
        }
        canvas.mini-orb {
          width: 28px;
          height: 28px;
          display: block;
        }
        .name {
          font-size: 16px;
          font-weight: 600;
          line-height: 1.2;
        }
        .status-pill {
          font-size: 12px;
          color: var(--secondary-text-color, #6b7280);
          font-weight: 500;
        }
        .action-button {
          border: none;
          outline: none;
          padding: 8px 18px;
          border-radius: 999px;
          font-size: 13px;
          font-weight: 600;
          cursor: pointer;
          background: ${running ? "var(--error-color, #ef4444)" : accentHex};
          color: #ffffff;
          box-shadow: 0 4px 14px rgba(${running ? "239, 68, 68" : `${accent.r}, ${accent.g}, ${accent.b}`}, 0.28);
          transition: transform 120ms ease, box-shadow 120ms ease, filter 120ms ease;
        }
        .action-button:hover {
          filter: brightness(1.05);
          transform: translateY(-1px);
        }
        .action-button:active {
          transform: scale(0.97);
        }
        .transcript-container {
          flex: 1;
          padding: 18px 20px;
          overflow-y: auto;
          display: flex;
          flex-direction: column;
        }
        .message-list {
          display: flex;
          flex-direction: column;
          gap: 12px;
          margin-top: auto;
        }
        .empty-state {
          margin: auto;
          text-align: center;
          color: var(--secondary-text-color, #9ca3af);
          font-size: 15px;
          font-weight: 500;
          padding: 24px 0;
        }
        .msg-bubble {
          max-width: 82%;
          padding: 10px 15px;
          border-radius: 18px;
          font-size: 14.5px;
          line-height: 1.45;
          word-wrap: break-word;
          animation: bubble-fade 200ms ease;
        }
        .msg-bubble.user {
          align-self: flex-end;
          background: rgba(${accent.r}, ${accent.g}, ${accent.b}, 0.12);
          color: var(--primary-text-color, #1f2937);
          border-bottom-right-radius: 4px;
        }
        .msg-bubble.assistant {
          align-self: flex-start;
          background: var(--secondary-background-color, rgba(127, 127, 127, 0.08));
          color: var(--primary-text-color, #111827);
          border-bottom-left-radius: 4px;
        }
        @keyframes bubble-fade {
          from { opacity: 0; transform: translateY(6px); }
          to { opacity: 1; transform: translateY(0); }
        }
        .audio-unblock {
          background: #fef3c7;
          color: #92400e;
          padding: 8px 16px;
          font-size: 12px;
          text-align: center;
          cursor: pointer;
          font-weight: 600;
        }
        audio { display: none; }
      </style>
      <ha-card>
        <div class="header">
          <div class="title-group">
            <canvas class="mini-orb"></canvas>
            <div>
              <div class="name">${this.config.name || "Pipecat Companion"}</div>
              <div class="status-pill">${status}</div>
            </div>
          </div>
          <button class="action-button">${running ? this.t("stop") : this.t("talk")}</button>
        </div>
        ${this.audioBlocked ? `<div class="audio-unblock">${this.t("audioBlocked")}</div>` : ""}
        <div class="transcript-container">
          <div class="message-list"></div>
        </div>
        <audio autoplay playsinline></audio>
      </ha-card>
    `;

    this.audio = this.shadowRoot.querySelector("audio");
    this.ensureVisualizer();
    this.attachAudio();
    this.renderTranscript();

    const btn = this.shadowRoot.querySelector(".action-button");
    if (btn) btn.onclick = () => running ? this.stop() : this.start();

    const unblock = this.shadowRoot.querySelector(".audio-unblock");
    if (unblock) {
      unblock.onclick = () => {
        this.audioBlocked = false;
        this.attachAudio();
        this.render();
      };
    }
  }
}

customElements.define("pipecat-humane-card", PipecatHumaneCard);

window.customCards = Array.isArray(window.customCards) ? window.customCards : [];
if (!window.customCards.some((c) => c.type === "pipecat-humane-card")) {
  window.customCards.push({
    type: "pipecat-humane-card",
    name: "Pipecat Humane Card",
    description: "A warm, simplistic conversational text card for Pipecat Assist.",
    preview: true,
  });
}
