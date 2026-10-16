/**
 * Pipecat Wave Card - A minimalist, human-centered conversational card
 * with dual-source background soundwaves, centered streaming transcript,
 * and rounded icon-style activity controls for voice and camera.
 */

const PIPECAT_WAVE_CARD_VERSION = "1.0.0";
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

const WAVE_TRANSLATIONS = {
  en: {
    greeting: "How can I help you today?",
    ready: "Ready",
    connecting: "Connecting...",
    listening: "Listening...",
    speaking: "Speaking...",
    error: "Connection error",
    you: "You",
    assistant: "Pipecat",
    audioBlocked: "Audio blocked. Tap to enable playback.",
    microphoneUnavailable: "Microphone access unavailable.",
    microphoneBlocked: "Microphone permission blocked.",
    cameraUnavailable: "Camera access unavailable.",
    tapToTalk: "Start conversation",
    tapToStop: "Stop conversation",
    toggleCamera: "Toggle camera vision",
  },
  pl: {
    greeting: "W czym mogę pomóc?",
    ready: "Gotowy",
    connecting: "Łączenie...",
    listening: "Słucham...",
    speaking: "Mówię...",
    error: "Błąd połączenia",
    you: "Ty",
    assistant: "Pipecat",
    audioBlocked: "Dźwięk zablokowany. Dotknij, aby włączyć.",
    microphoneUnavailable: "Brak dostępu do mikrofonu.",
    microphoneBlocked: "Dostęp do mikrofonu zablokowany.",
    cameraUnavailable: "Brak dostępu do kamery.",
    tapToTalk: "Rozpocznij rozmowę",
    tapToStop: "Zakończ rozmowę",
    toggleCamera: "Przełącz kamerę",
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

// Minimal, clean SVG icons
const ICONS = {
  mic: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
    <path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z"/>
    <path d="M19 10v2a7 7 0 0 1-14 0v-2"/>
    <line x1="12" y1="19" x2="12" y2="22"/>
  </svg>`,
  micActive: `<svg viewBox="0 0 24 24" fill="currentColor">
    <path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z"/>
    <path fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" d="M19 10v2a7 7 0 0 1-14 0v-2M12 19v3"/>
  </svg>`,
  camera: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
    <path d="M14.5 4h-5L7 7H4a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V9a2 2 0 0 0-2-2h-3l-2.5-3z"/>
    <circle cx="12" cy="13" r="3"/>
  </svg>`,
  cameraActive: `<svg viewBox="0 0 24 24" fill="currentColor">
    <path d="M14.5 4h-5L7 7H4a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V9a2 2 0 0 0-2-2h-3l-2.5-3z"/>
    <circle cx="12" cy="13" r="3" fill="var(--ha-card-background, #ffffff)"/>
  </svg>`,
};

class PipecatWaveCard extends HTMLElement {
  constructor() {
    super();
    this.state = "idle"; // idle | connecting | connected | error
    this.detail = "";
    this.messages = []; // { id, role: 'user' | 'assistant', text }
    this.messageSeq = 0;
    this.audioBlocked = false;
    this.cameraActive = false;
    this.cameraStream = null;
    this.userEnergy = 0;
    this.agentEnergy = 0;
    this.stopOnPageExit = () => this.stop();
  }

  static getStubConfig() {
    return { name: "Pipecat Live" };
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
    return 4;
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
    return WAVE_TRANSLATIONS[lang]?.[key] || WAVE_TRANSLATIONS.en[key] || key;
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
    const key = "pipecat-wave-card-client-id";
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
    if (this.state === "error") return this.detail || this.t("error");
    if (this.state === "connecting") return this.t("connecting");
    if (this.state === "connected") {
      if (this.agentEnergy > 0.05) return this.t("speaking");
      if (this.userEnergy > 0.05) return this.t("listening");
      return this.t("ready");
    }
    return this.t("ready");
  }

  async loadAddonConfig() {
    const path = this.proxyMode()
      ? `/api/pipecat_live/config${this.entryQuery()}`
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
    if (this.messages.length > 25) this.messages.shift();
    this.renderTranscript();
    return id;
  }

  updateMessage(id, text) {
    const msg = this.messages.find((m) => m.id === id);
    if (!msg) return;
    msg.text = normalizeTranscriptText(text);
    const bubble = this.shadowRoot?.querySelector(`[data-msg-id="${id}"] .msg-text`);
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
        <div class="msg-card ${m.role}" data-msg-id="${m.id}">
          <div class="msg-author">
            <span class="author-tag">${m.role === "user" ? escapeHtml(this.t("you")) : escapeHtml(this.t("assistant"))}</span>
          </div>
          <div class="msg-text">${escapeHtml(m.text)}</div>
        </div>
      `
      )
      .join("");
    this.scrollToBottom();
  }

  scrollToBottom() {
    const scrollContainer = this.shadowRoot?.querySelector(".transcript-viewport");
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

  updateCameraVideo() {
    const videoEl = this.shadowRoot?.querySelector("video.camera-video");
    const container = this.shadowRoot?.querySelector(".camera-preview-container");
    if (container) {
      if (this.cameraActive && this.cameraStream) {
        container.classList.add("visible");
      } else {
        container.classList.remove("visible");
      }
    }
    if (videoEl) {
      if (this.cameraActive && this.cameraStream) {
        if (videoEl.srcObject !== this.cameraStream) {
          videoEl.srcObject = this.cameraStream;
          videoEl.play().catch(() => {});
        }
      } else {
        videoEl.pause();
        videoEl.srcObject = null;
      }
    }
  }

  async toggleCamera(e) {
    if (e) e.stopPropagation();

    if (this.cameraActive) {
      // Deactivate camera
      this.cameraStream?.getTracks().forEach((t) => t.stop());
      this.cameraStream = null;
      this.cameraActive = false;
      if (this.videoSender && this.peer) {
        try { this.peer.removeTrack(this.videoSender); } catch {}
        this.videoSender = null;
      }
      this.updateControls();
      this.updateCameraVideo();
      return;
    }

    // Activate camera
    try {
      this.cameraStream = await navigator.mediaDevices.getUserMedia({
        video: { width: { ideal: 640 }, height: { ideal: 480 }, frameRate: { ideal: 15 } },
      });
      this.cameraActive = true;
      const videoTrack = this.cameraStream.getVideoTracks()[0];
      if (this.peer && videoTrack) {
        this.videoSender = this.peer.addTrack(videoTrack, this.cameraStream);
      }
      this.updateControls();
      this.updateCameraVideo();
    } catch (err) {
      console.warn("Camera access denied or unavailable", err);
      this.cameraActive = false;
      this.updateControls();
      this.updateCameraVideo();
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
      const constraints = {
        audio: { autoGainControl: true, echoCancellation: true, noiseSuppression: true },
        video: this.cameraActive ? { width: { ideal: 640 }, height: { ideal: 480 }, frameRate: { ideal: 15 } } : false,
      };

      this.stream = await navigator.mediaDevices.getUserMedia(constraints);
      if (this.cameraActive) {
        this.cameraStream = this.stream;
        this.updateCameraVideo();
      }

      this.ensureVisualizer();

      const peer = new RTCPeerConnection();
      this.peer = peer;

      const audioTrack = this.stream.getAudioTracks()[0];
      if (audioTrack) peer.addTransceiver(audioTrack, { direction: "sendrecv" });
      else peer.addTransceiver("audio", { direction: "sendrecv" });

      if (this.cameraActive) {
        const videoTrack = this.stream.getVideoTracks()[0];
        if (videoTrack) this.videoSender = peer.addTrack(videoTrack, this.stream);
      }

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
              library: "pipecat-wave-card",
              library_version: PIPECAT_WAVE_CARD_VERSION,
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
          this.updateControls();
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
        ? `/api/pipecat_live/offer${this.entryQuery()}`
        : addonConfig.runner_offer_path || "api/offer";

      const requestData = {
        source: "wave_card",
        client_id: this.clientId(),
        language: this.sessionLanguage(),
        has_video: this.cameraActive,
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
    } catch (err) {
      this.state = "error";
      this.detail = err?.message || this.t("error");
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

    if (this.cameraStream && this.cameraStream !== this.stream) {
      this.cameraStream.getTracks().forEach((t) => t.stop());
    }
    this.cameraStream = null;
    this.cameraActive = false;
    this.updateCameraVideo();

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
      analyser.fftSize = 256;
      analyser.smoothingTimeConstant = 0.82;
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
    this.visualizerCanvas = this.shadowRoot?.querySelector("canvas.wave-bg");
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
    this.userEnergy = 0;
    this.agentEnergy = 0;
  }

  getEnergy(name) {
    const input = this.visualizerInputs?.[name];
    if (!input?.analyser) return 0;
    input.analyser.getByteFrequencyData(input.data);
    let sum = 0;
    const len = Math.min(input.data.length, 48);
    for (let i = 0; i < len; i++) sum += input.data[i];
    return Math.min(1, sum / len / 135);
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

      // Read audio energy
      const rawUserE = this.getEnergy("local");
      const rawAgentE = this.getEnergy("remote");
      const isConnected = this.state === "connected";
      const isConnecting = this.state === "connecting";

      // Smooth energies
      const idleE = isConnected ? 0.03 : (isConnecting ? 0.08 : 0.015);
      this.userEnergy = (this.userEnergy || 0) * 0.8 + Math.max(rawUserE, idleE) * 0.2;
      this.agentEnergy = (this.agentEnergy || 0) * 0.8 + Math.max(rawAgentE, idleE) * 0.2;

      const time = performance.now() / 1000;
      ctx.clearRect(0, 0, w, h);

      const centerY = h * 0.65; // Baseline below middle so centered text sits above

      // Helper to draw a calm harmonic wave
      const renderWave = (r, g, b, alpha, offset, amp, freq, speed) => {
        ctx.beginPath();
        const step = Math.max(2, Math.floor(w / 80));
        for (let x = 0; x <= w; x += step) {
          const progress = x / w;
          const envelope = Math.sin(progress * Math.PI); // Pin ends to zero
          const y = centerY
            + Math.sin(progress * Math.PI * freq + time * speed + offset) * amp * envelope
            + Math.sin(progress * Math.PI * (freq * 1.8) - time * (speed * 0.7) - offset) * (amp * 0.35) * envelope;
          if (x === 0) ctx.moveTo(x, y);
          else ctx.lineTo(x, y);
        }
        ctx.strokeStyle = `rgba(${r}, ${g}, ${b}, ${alpha.toFixed(3)})`;
        ctx.lineWidth = Math.max(1.2, 1.8 * dpr);
        ctx.stroke();
      };

      // 1. Agent wave (Muted theme accent, reacts to agent speech)
      const agentAmp = (6 * dpr) + (this.agentEnergy * 32 * dpr);
      const agentAlpha = 0.22 + (this.agentEnergy * 0.38);
      renderWave(accent.r, accent.g, accent.b, agentAlpha, 0, agentAmp, 3.2, 1.8);
      renderWave(accent.r, accent.g, accent.b, agentAlpha * 0.6, 1.4, agentAmp * 0.75, 4.8, 1.2);

      // 2. User wave (Complementary muted slate/teal, reacts to user microphone)
      const userR = Math.max(0, accent.r - 40);
      const userG = Math.min(255, accent.g + 30);
      const userB = Math.min(255, accent.b + 50);
      const userAmp = (5 * dpr) + (this.userEnergy * 30 * dpr);
      const userAlpha = 0.18 + (this.userEnergy * 0.4);
      renderWave(userR, userG, userB, userAlpha, 2.5, userAmp, 3.8, -1.6);
      renderWave(userR, userG, userB, userAlpha * 0.5, 3.9, userAmp * 0.7, 5.2, -1.1);
    }

    this.visualizerFrame = requestAnimationFrame(() => this.drawVisualizer());
  }

  updateControls() {
    const micBtn = this.shadowRoot?.querySelector(".control-card.voice");
    const camBtn = this.shadowRoot?.querySelector(".control-card.camera");
    const statusEl = this.shadowRoot?.querySelector(".status-text");

    const isConnected = this.state === "connected";
    const isConnecting = this.state === "connecting";
    const isRunning = isConnected || isConnecting;

    if (micBtn) {
      micBtn.className = `control-card voice ${isRunning ? "active" : ""} ${isConnecting ? "connecting" : ""}`;
      micBtn.innerHTML = isRunning ? ICONS.micActive : ICONS.mic;
      micBtn.title = isRunning ? this.t("tapToStop") : this.t("tapToTalk");
    }

    if (camBtn) {
      camBtn.className = `control-card camera ${this.cameraActive ? "active" : ""}`;
      camBtn.innerHTML = this.cameraActive ? ICONS.cameraActive : ICONS.camera;
      camBtn.title = this.t("toggleCamera");
    }

    if (statusEl) {
      statusEl.textContent = this.statusLabel();
    }
  }

  render() {
    if (!this.shadowRoot) return;
    const accentHex = this.accentHex();
    const accent = this.accentRgb();
    const isConnected = this.state === "connected";
    const isConnecting = this.state === "connecting";
    const isRunning = isConnected || isConnecting;
    const status = this.statusLabel();

    this.shadowRoot.innerHTML = `
      <style>
        ha-card {
          display: flex;
          flex-direction: column;
          height: 440px;
          border-radius: 24px;
          background: var(--ha-card-background, var(--card-background-color, #ffffff));
          color: var(--primary-text-color, #1f2937);
          border: 1px solid var(--divider-color, rgba(127, 127, 127, 0.14));
          box-shadow: 0 4px 20px rgba(0, 0, 0, 0.04);
          overflow: hidden;
          position: relative;
          box-sizing: border-box;
          font-family: var(--paper-font-body1_-_font-family, system-ui, -apple-system, sans-serif);
        }

        /* Top Header */
        .card-header {
          display: flex;
          align-items: center;
          justify-content: space-between;
          padding: 16px 20px 12px;
          z-index: 2;
        }
        .header-title {
          font-size: 15px;
          font-weight: 600;
          letter-spacing: -0.01em;
          color: var(--primary-text-color, #1f2937);
        }
        .status-text {
          font-size: 12.5px;
          color: var(--secondary-text-color, #6b7280);
          font-weight: 500;
        }

        /* Background Wave Canvas */
        canvas.wave-bg {
          position: absolute;
          top: 0;
          left: 0;
          width: 100%;
          height: 100%;
          pointer-events: none;
          z-index: 1;
        }

        /* Camera Live Preview */
        .camera-preview-container {
          display: none;
          width: 100%;
          max-width: 88%;
          margin: 4px auto 10px;
          height: 140px;
          border-radius: 16px;
          overflow: hidden;
          position: relative;
          background: rgba(0, 0, 0, 0.15);
          border: 1px solid var(--divider-color, rgba(127, 127, 127, 0.15));
          box-shadow: 0 4px 14px rgba(0, 0, 0, 0.06);
          flex-shrink: 0;
          animation: cam-fade 200ms ease;
        }
        .camera-preview-container.visible {
          display: block;
        }
        .camera-video {
          width: 100%;
          height: 100%;
          object-fit: cover;
          display: block;
          transform: scaleX(-1);
        }
        .camera-live-badge {
          position: absolute;
          top: 8px;
          left: 10px;
          display: flex;
          align-items: center;
          gap: 6px;
          padding: 3px 8px;
          border-radius: 999px;
          background: rgba(0, 0, 0, 0.45);
          color: #ffffff;
          font-size: 10.5px;
          font-weight: 600;
          letter-spacing: 0.04em;
          text-transform: uppercase;
          backdrop-filter: blur(4px);
          -webkit-backdrop-filter: blur(4px);
        }
        .cam-dot {
          width: 6px;
          height: 6px;
          border-radius: 50%;
          background: #10b981;
          box-shadow: 0 0 6px #10b981;
        }
        @keyframes cam-fade {
          from { opacity: 0; transform: translateY(-4px); }
          to { opacity: 1; transform: translateY(0); }
        }

        /* Centered Conversation Viewport */
        .transcript-viewport {
          flex: 1;
          padding: 8px 16px;
          overflow-y: auto;
          display: flex;
          flex-direction: column;
          position: relative;
          z-index: 2;
          scrollbar-width: thin;
          scrollbar-color: rgba(127, 127, 127, 0.2) transparent;
        }
        .message-list {
          display: flex;
          flex-direction: column;
          gap: 12px;
          margin-top: auto;
          width: 100%;
          max-width: 88%;
          margin-left: auto;
          margin-right: auto;
          padding-bottom: 8px;
        }
        .empty-state {
          margin: auto;
          text-align: center;
          color: var(--secondary-text-color, #9ca3af);
          font-size: 14.5px;
          font-weight: 400;
          padding: 30px 16px;
        }

        /* Centered Card Messages */
        .msg-card {
          width: 100%;
          padding: 10px 14px;
          border-radius: 14px;
          background: var(--ha-card-background, rgba(255, 255, 255, 0.85));
          backdrop-filter: blur(8px);
          -webkit-backdrop-filter: blur(8px);
          border: 1px solid var(--divider-color, rgba(127, 127, 127, 0.12));
          box-shadow: 0 2px 8px rgba(0, 0, 0, 0.03);
          box-sizing: border-box;
          animation: msg-appear 180ms ease-out;
        }
        .msg-card.assistant {
          border-left: 3px solid rgba(${accent.r}, ${accent.g}, ${accent.b}, 0.6);
        }
        .msg-card.user {
          border-left: 3px solid rgba(100, 116, 139, 0.5);
        }
        .msg-author {
          font-size: 11px;
          font-weight: 600;
          text-transform: uppercase;
          letter-spacing: 0.04em;
          margin-bottom: 4px;
          color: var(--secondary-text-color, #64748b);
        }
        .msg-card.assistant .msg-author {
          color: rgba(${accent.r}, ${accent.g}, ${accent.b}, 0.9);
        }
        .msg-text {
          font-size: 14px;
          line-height: 1.48;
          color: var(--primary-text-color, #1e293b);
          word-wrap: break-word;
        }

        @keyframes msg-appear {
          from { opacity: 0; transform: translateY(4px); }
          to { opacity: 1; transform: translateY(0); }
        }

        /* Bottom Controls Dock */
        .controls-dock {
          display: flex;
          align-items: center;
          justify-content: center;
          gap: 16px;
          padding: 12px 20px 18px;
          z-index: 2;
          background: linear-gradient(to top, var(--ha-card-background, #ffffff) 65%, transparent);
        }

        /* Rounded Icon Style Cards */
        .control-card {
          width: 48px;
          height: 48px;
          border-radius: 50%;
          border: 1px solid var(--divider-color, rgba(127, 127, 127, 0.15));
          background: var(--card-background-color, rgba(127, 127, 127, 0.07));
          color: var(--secondary-text-color, #64748b);
          display: flex;
          align-items: center;
          justify-content: center;
          cursor: pointer;
          outline: none;
          position: relative;
          transition: transform 140ms ease, background 160ms ease, border-color 160ms ease, color 160ms ease;
          box-shadow: 0 2px 6px rgba(0, 0, 0, 0.03);
        }
        .control-card svg {
          width: 22px;
          height: 22px;
          transition: transform 140ms ease;
        }
        .control-card:hover {
          transform: translateY(-1px);
          color: var(--primary-text-color, #1e293b);
          border-color: rgba(127, 127, 127, 0.3);
        }
        .control-card:active {
          transform: scale(0.95);
        }

        /* Active Voice State */
        .control-card.voice.active {
          background: rgba(${accent.r}, ${accent.g}, ${accent.b}, 0.12);
          border-color: rgba(${accent.r}, ${accent.g}, ${accent.b}, 0.4);
          color: ${accentHex};
          box-shadow: 0 0 0 3px rgba(${accent.r}, ${accent.g}, ${accent.b}, 0.15);
        }
        .control-card.voice.connecting {
          animation: soft-pulse 1.4s infinite ease-in-out;
        }

        /* Active Camera State */
        .control-card.camera.active {
          background: rgba(16, 185, 129, 0.12);
          border-color: rgba(16, 185, 129, 0.45);
          color: #10b981;
          box-shadow: 0 0 0 3px rgba(16, 185, 129, 0.15);
        }

        @keyframes soft-pulse {
          0%, 100% { transform: scale(1); box-shadow: 0 0 0 0 rgba(${accent.r}, ${accent.g}, ${accent.b}, 0.2); }
          50% { transform: scale(1.04); box-shadow: 0 0 0 5px rgba(${accent.r}, ${accent.g}, ${accent.b}, 0); }
        }

        /* Audio blocked warning */
        .audio-unblock {
          background: rgba(245, 158, 11, 0.12);
          color: #b45309;
          padding: 6px 12px;
          font-size: 12px;
          text-align: center;
          cursor: pointer;
          font-weight: 500;
          z-index: 3;
        }
        audio { display: none; }
      </style>
      <ha-card>
        <div class="card-header">
          <div class="header-title">${escapeHtml(this.config.name || "Pipecat Live")}</div>
          <div class="status-text">${escapeHtml(status)}</div>
        </div>

        <canvas class="wave-bg"></canvas>

        ${this.audioBlocked ? `<div class="audio-unblock">${this.t("audioBlocked")}</div>` : ""}

        <div class="transcript-viewport">
          <div class="camera-preview-container ${this.cameraActive && this.cameraStream ? "visible" : ""}">
            <video class="camera-video" autoplay playsinline muted></video>
            <div class="camera-live-badge"><span class="cam-dot"></span>Live</div>
          </div>
          <div class="message-list"></div>
        </div>

        <div class="controls-dock">
          <button class="control-card voice ${isRunning ? "active" : ""} ${isConnecting ? "connecting" : ""}" title="${isRunning ? this.t("tapToStop") : this.t("tapToTalk")}">
            ${isRunning ? ICONS.micActive : ICONS.mic}
          </button>
          <button class="control-card camera ${this.cameraActive ? "active" : ""}" title="${this.t("toggleCamera")}">
            ${this.cameraActive ? ICONS.cameraActive : ICONS.camera}
          </button>
        </div>

        <audio autoplay playsinline></audio>
      </ha-card>
    `;

    this.audio = this.shadowRoot.querySelector("audio");
    this.ensureVisualizer();
    this.attachAudio();
    this.renderTranscript();
    this.updateCameraVideo();

    const voiceBtn = this.shadowRoot.querySelector(".control-card.voice");
    if (voiceBtn) voiceBtn.onclick = () => isRunning ? this.stop() : this.start();

    const cameraBtn = this.shadowRoot.querySelector(".control-card.camera");
    if (cameraBtn) cameraBtn.onclick = (e) => this.toggleCamera(e);

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

if (!customElements.get("pipecat-live-wave-card")) {
  customElements.define("pipecat-live-wave-card", PipecatWaveCard);
}
if (!customElements.get("pipecat-wave-card")) {
  customElements.define("pipecat-wave-card", PipecatWaveCard);
}

window.customCards = Array.isArray(window.customCards) ? window.customCards : [];
if (!window.customCards.some((c) => c.type === "pipecat-live-wave-card")) {
  window.customCards.push({
    type: "pipecat-live-wave-card",
    name: "Pipecat Live Wave Card",
    description: "Minimalist conversational card with dual background soundwaves and rounded control cards.",
    preview: true,
  });
}
