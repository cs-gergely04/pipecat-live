/**
 * Pipecat Orb Card - Circular, tactile voice assistant card with 3 visual modes:
 *  - "blob"   : Quantum iridescent morphing 3D glass core
 *  - "avatar" : Minimalist expressive Pixar/EVA character eyes
 *  - "aura"   : Organic breathing harmonic aura
 *
 * Plus optional Multimodal Camera Vision integration (streams webcam/video track to Gemini Live).
 */

const PIPECAT_ORB_CARD_VERSION = "1.2.0";
const DEFAULT_ACCENT_HEX = "#3b82f6";
const DEFAULT_AUDIO_BUFFER_MS = 120;

const OPUS_AUDIO_QUALITY_PARAMS = {
  minptime: "20",
  useinbandfec: "1",
  maxplaybackrate: "48000",
  maxaveragebitrate: "96000",
  usedtx: "0",
};
const OPUS_AUDIO_REMOVE_PARAMS = new Set(["stereo", "sprop-stereo"]);

const ORB_TRANSLATIONS = {
  en: {
    ready: "Ready",
    tapToTalk: "Tap to talk",
    listening: "Listening...",
    thinking: "Thinking...",
    speaking: "Speaking...",
    connecting: "Connecting...",
    error: "Error",
    tapToStop: "Tap to stop",
    enableAudio: "Enable audio",
    audioBlocked: "Tap to enable sound",
    microphoneUnavailable: "Microphone unavailable",
    microphoneBlocked: "Microphone blocked",
    cameraOn: "Camera active",
    cameraOff: "Camera off",
  },
  pl: {
    ready: "Gotowy",
    tapToTalk: "Dotknij, aby rozmawiać",
    listening: "Słucham...",
    thinking: "Myślę...",
    speaking: "Mówię...",
    connecting: "Łączenie...",
    error: "Błąd",
    tapToStop: "Dotknij, aby zakończyć",
    enableAudio: "Włącz dźwięk",
    audioBlocked: "Dotknij, aby włączyć dźwięk",
    microphoneUnavailable: "Mikrofon niedostępny",
    microphoneBlocked: "Mikrofon zablokowany",
    cameraOn: "Kamera aktywna",
    cameraOff: "Kamera wyłączona",
  },
};

function languageBase(value) {
  return String(value || "").toLowerCase().split(/[-_]/)[0] || "en";
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

function clamp(value, min, max) {
  return Math.min(max, Math.max(min, value));
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

class PipecatOrbCard extends HTMLElement {
  constructor() {
    super();
    this.state = "idle"; // idle | connecting | connected | error
    this.detail = "";
    this.audioBlocked = false;
    this.botSpeaking = false;
    this.userSpeaking = false;
    this.visualizerEnergy = 0;
    this.cameraActive = false;
    this.cameraStream = null;

    // Avatar eye animation physics
    this.avatarBlink = 0;
    this.avatarLastBlink = Date.now();
    this.avatarLookX = 0;
    this.avatarLookY = 0;
    this.avatarTargetLookX = 0;
    this.avatarTargetLookY = 0;
    this.avatarNextLookShift = Date.now() + 2000;

    this.stopOnPageExit = () => this.stop();
  }

  static getStubConfig() {
    return { name: "Pipecat", mode: "avatar", size: 280, enable_camera: true };
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
    return ORB_TRANSLATIONS[lang]?.[key] || ORB_TRANSLATIONS.en[key] || key;
  }

  accentHex() {
    return normalizeHexColor(this.config.accent_color || this.config.color);
  }

  accentRgb() {
    return hexToRgb(this.accentHex());
  }

  visualMode() {
    // "avatar" | "blob" | "aura"
    return this.config.mode || "avatar";
  }

  orbSize() {
    const s = Number(this.config.size || 280);
    return clamp(isNaN(s) ? 280 : s, 180, 500);
  }

  isCameraEnabled() {
    return this.config.enable_camera !== false;
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
    const key = "pipecat-orb-card-client-id";
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
    if (this.audioBlocked) return this.t("audioBlocked");
    if (this.state === "connecting") return this.t("connecting");
    if (this.state === "connected") {
      if (this.botSpeaking) return this.t("speaking");
      if (this.userSpeaking) return this.t("listening");
      return this.t("listening");
    }
    return this.t("tapToTalk");
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

  handleRealtimeMessage(raw) {
    if (typeof raw !== "string" || !raw.trim().startsWith("{")) return;
    let msg;
    try { msg = JSON.parse(raw); } catch { return; }
    const type = String(msg.type || msg.event || msg.name || "").toLowerCase();
    if (["conversation-ended", "conversation_end", "end-conversation"].includes(type)) {
      this.stop();
      return;
    }
    if (type === "bot-llm-started" || type === "bot-tts-started" || type === "bot-started-speaking") {
      this.botSpeaking = true;
      this.userSpeaking = false;
      this.updateStatusText();
    }
    if (type === "bot-llm-stopped" || type === "bot-tts-stopped" || type === "bot-stopped-speaking") {
      this.botSpeaking = false;
      this.updateStatusText();
    }
    if (type === "user-started-speaking") {
      this.userSpeaking = true;
      this.botSpeaking = false;
      this.updateStatusText();
    }
    if (type === "user-stopped-speaking") {
      this.userSpeaking = false;
      this.updateStatusText();
    }
  }

  updateStatusText() {
    const el = this.shadowRoot?.querySelector(".status-text");
    if (el) el.textContent = this.statusLabel();
  }

  cycleMode(e) {
    e.stopPropagation();
    const modes = ["avatar", "blob", "aura"];
    const curIdx = modes.indexOf(this.visualMode());
    const nextMode = modes[(curIdx + 1) % modes.length];
    this.config.mode = nextMode;
    this.render();
  }

  async toggleCamera(e) {
    e.stopPropagation();
    if (!this.isCameraEnabled()) return;

    if (this.cameraActive) {
      // Turn off
      this.cameraStream?.getTracks().forEach((t) => t.stop());
      this.cameraStream = null;
      this.cameraActive = false;
      if (this.videoSender && this.peer) {
        try { this.peer.removeTrack(this.videoSender); } catch {}
        this.videoSender = null;
      }
      this.render();
      return;
    }

    // Turn on webcam / camera
    try {
      this.cameraStream = await navigator.mediaDevices.getUserMedia({
        video: { width: { ideal: 640 }, height: { ideal: 480 }, frameRate: { ideal: 15 } },
      });
      this.cameraActive = true;
      const videoTrack = this.cameraStream.getVideoTracks()[0];
      if (this.peer && videoTrack) {
        this.videoSender = this.peer.addTrack(videoTrack, this.cameraStream);
      }
      this.render();
    } catch (err) {
      console.warn("Camera access denied or unavailable", err);
      this.cameraActive = false;
      this.render();
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
              library: "pipecat-orb-card",
              library_version: PIPECAT_ORB_CARD_VERSION,
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
        ? `/api/pipecat_live/offer${this.entryQuery()}`
        : addonConfig.runner_offer_path || "api/offer";

      const requestData = {
        source: "orb_card",
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
    if (this.cameraStream && this.cameraStream !== this.stream) {
      this.cameraStream.getTracks().forEach((t) => t.stop());
    }
    this.cameraStream = null;
    this.remoteStream?.getTracks().forEach((t) => t.stop());
    this.remoteStream = undefined;
    this.resetAudioElement();
    this.stopVisualizer();
    this.botSpeaking = false;
    this.userSpeaking = false;
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
    this.visualizerCanvas = this.shadowRoot?.querySelector("canvas.orb-canvas");
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
    const len = Math.min(input.data.length, 48);
    for (let i = 0; i < len; i++) sum += input.data[i];
    return Math.min(1, sum / len / 140);
  }

  drawVisualizer() {
    const canvas = this.visualizerCanvas;
    if (!canvas) {
      this.visualizerFrame = requestAnimationFrame(() => this.drawVisualizer());
      return;
    }

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

    if (localE > 0.08) this.userSpeaking = true;
    else if (localE < 0.02 && !this.botSpeaking) this.userSpeaking = false;

    const idleE = this.state === "connecting" ? 0.22 : 0.06;
    const targetE = Math.max(localE, remoteE, idleE);
    this.visualizerEnergy = (this.visualizerEnergy || 0) * 0.8 + targetE * 0.2;
    const energy = this.visualizerEnergy;
    const time = performance.now() / 1000;

    ctx.clearRect(0, 0, w, h);
    const cx = w / 2;
    const cy = h / 2;
    const mode = this.visualMode();

    if (mode === "avatar") {
      this.drawAvatar(ctx, cx, cy, w, h, dpr, energy, time, running, accent);
    } else if (mode === "blob") {
      this.drawQuantumBlob(ctx, cx, cy, w, h, dpr, energy, time, running, accent);
    } else {
      this.drawOrganicAura(ctx, cx, cy, w, h, dpr, energy, time, running, accent);
    }

    this.visualizerFrame = requestAnimationFrame(() => this.drawVisualizer());
  }

  /* ---------------- MODE 1: MINIMALIST CHARACTER AVATAR ---------------- */
  drawAvatar(ctx, cx, cy, w, h, dpr, energy, time, running, accent) {
    const now = Date.now();
    // Blink mechanics (every 3 to 6 seconds)
    if (now - this.avatarLastBlink > 3500 + (Math.sin(time) * 1500)) {
      this.avatarBlink = 1;
      this.avatarLastBlink = now;
    }
    if (this.avatarBlink > 0) {
      this.avatarBlink -= 0.12;
      if (this.avatarBlink < 0) this.avatarBlink = 0;
    }

    // Natural eye look-around shift
    if (now > this.avatarNextLookShift) {
      this.avatarTargetLookX = (Math.random() - 0.5) * 12 * dpr;
      this.avatarTargetLookY = (Math.random() - 0.5) * 8 * dpr;
      this.avatarNextLookShift = now + 2000 + Math.random() * 3000;
    }
    this.avatarLookX += (this.avatarTargetLookX - this.avatarLookX) * 0.08;
    this.avatarLookY += (this.avatarTargetLookY - this.avatarLookY) * 0.08;

    // Outer subtle ambient glow
    const baseRadius = (Math.min(w, h) / 2) * 0.65;
    const outerGlow = ctx.createRadialGradient(cx, cy, baseRadius * 0.4, cx, cy, baseRadius * 1.15);
    outerGlow.addColorStop(0, `rgba(${accent.r}, ${accent.g}, ${accent.b}, ${0.12 + energy * 0.2})`);
    outerGlow.addColorStop(1, "rgba(0, 0, 0, 0)");
    ctx.fillStyle = outerGlow;
    ctx.beginPath();
    ctx.arc(cx, cy, baseRadius * 1.15, 0, Math.PI * 2);
    ctx.fill();

    // Dark sleek visor face background
    ctx.save();
    ctx.beginPath();
    ctx.arc(cx, cy, baseRadius, 0, Math.PI * 2);
    const visorGrad = ctx.createRadialGradient(cx, cy - baseRadius * 0.2, baseRadius * 0.1, cx, cy, baseRadius);
    visorGrad.addColorStop(0, "#1f2430");
    visorGrad.addColorStop(1, "#0a0c12");
    ctx.fillStyle = visorGrad;
    ctx.shadowColor = `rgba(${accent.r}, ${accent.g}, ${accent.b}, ${0.35 + energy * 0.35})`;
    ctx.shadowBlur = 24 * dpr;
    ctx.fill();
    ctx.restore();

    // Eye specs
    const eyeSpacing = 28 * dpr;
    const eyeWidth = (20 + (running ? 2 : 0)) * dpr;
    const openFactor = 1 - Math.sin(this.avatarBlink * Math.PI);
    const eyeHeight = Math.max(2 * dpr, (28 + energy * 16) * dpr * openFactor);

    // Eye color: Radiant cyan/accent, warm amber when thinking, red on error
    let eyeColor = `rgb(${accent.r}, ${accent.g}, ${accent.b})`;
    if (this.state === "error") eyeColor = "#ef4444";
    else if (running && this.botSpeaking) eyeColor = "#67e8f9"; // vibrant cyan speaking
    else if (running) eyeColor = "#a78bfa"; // listening soft purple

    const drawEye = (x, y) => {
      ctx.save();
      ctx.translate(x + this.avatarLookX, y + this.avatarLookY);
      ctx.beginPath();
      ctx.ellipse(0, 0, eyeWidth / 2, eyeHeight / 2, 0, 0, Math.PI * 2);
      ctx.fillStyle = "#ffffff";
      ctx.shadowColor = eyeColor;
      ctx.shadowBlur = 18 * dpr;
      ctx.fill();

      // Eye pupil / core glow
      ctx.beginPath();
      ctx.ellipse(0, 0, (eyeWidth / 2) * 0.82, (eyeHeight / 2) * 0.85, 0, 0, Math.PI * 2);
      ctx.fillStyle = eyeColor;
      ctx.fill();
      ctx.restore();
    };

    drawEye(cx - eyeSpacing, cy - 4 * dpr);
    drawEye(cx + eyeSpacing, cy - 4 * dpr);

    // Dynamic speaking wave or smile beneath eyes
    if (running && this.botSpeaking) {
      ctx.save();
      ctx.beginPath();
      const mouthY = cy + 28 * dpr;
      const mouthW = 32 * dpr;
      ctx.moveTo(cx - mouthW, mouthY);
      for (let x = -mouthW; x <= mouthW; x += 4 * dpr) {
        const progress = (x + mouthW) / (mouthW * 2);
        const wave = Math.sin(progress * Math.PI * 4 + time * 8) * (energy * 10 * dpr);
        ctx.lineTo(cx + x, mouthY + wave);
      }
      ctx.strokeStyle = eyeColor;
      ctx.lineWidth = 2.5 * dpr;
      ctx.shadowColor = eyeColor;
      ctx.shadowBlur = 8 * dpr;
      ctx.stroke();
      ctx.restore();
    }
  }

  /* ---------------- MODE 2: QUANTUM IRIDESCENT 3D BLOB ---------------- */
  drawQuantumBlob(ctx, cx, cy, w, h, dpr, energy, time, running, accent) {
    const baseRadius = (Math.min(w, h) / 2) * 0.55;

    // Draw multi-layer iridescent floating fluid nodes
    const layers = [
      { color: `rgba(${accent.r}, ${accent.g}, ${accent.b}, 0.85)`, speed: 1.8, offset: 0, rMult: 1.0 },
      { color: "rgba(236, 72, 153, 0.75)", speed: -2.3, offset: 2.1, rMult: 0.88 }, // pink
      { color: "rgba(56, 189, 248, 0.75)", speed: 1.4, offset: 4.2, rMult: 0.8 }, // cyan
    ];

    layers.forEach((layer) => {
      ctx.save();
      ctx.beginPath();
      const points = 24;
      for (let i = 0; i <= points; i++) {
        const theta = (i / points) * Math.PI * 2;
        const wave1 = Math.sin(theta * 3 + time * layer.speed + layer.offset) * (energy * 24 * dpr);
        const wave2 = Math.cos(theta * 4 - time * 1.5) * (energy * 12 * dpr);
        const r = (baseRadius * layer.rMult) + wave1 + wave2;
        const x = cx + Math.cos(theta) * r;
        const y = cy + Math.sin(theta) * r;
        if (i === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
      }
      ctx.closePath();
      ctx.fillStyle = layer.color;
      ctx.shadowColor = layer.color;
      ctx.shadowBlur = (20 + energy * 30) * dpr;
      ctx.fill();
      ctx.restore();
    });

    // Glass refraction highlight over top
    ctx.save();
    ctx.beginPath();
    ctx.ellipse(cx - baseRadius * 0.25, cy - baseRadius * 0.28, baseRadius * 0.35, baseRadius * 0.16, -Math.PI / 4, 0, Math.PI * 2);
    ctx.fillStyle = "rgba(255, 255, 255, 0.5)";
    ctx.fill();
    ctx.restore();
  }

  /* ---------------- MODE 3: ORGANIC BREATHING AURA ---------------- */
  drawOrganicAura(ctx, cx, cy, w, h, dpr, energy, time, running, accent) {
    const baseRadius = (Math.min(w, h) / 2) * 0.58;

    // Outer aura glow
    const outerGlowRadius = baseRadius * (1.15 + energy * 0.35);
    const glowGrad = ctx.createRadialGradient(cx, cy, baseRadius * 0.4, cx, cy, outerGlowRadius);
    glowGrad.addColorStop(0, `rgba(${accent.r}, ${accent.g}, ${accent.b}, ${0.28 + energy * 0.42})`);
    glowGrad.addColorStop(0.65, `rgba(${accent.r}, ${accent.g}, ${accent.b}, ${0.12 + energy * 0.2})`);
    glowGrad.addColorStop(1, `rgba(${accent.r}, ${accent.g}, ${accent.b}, 0)`);
    ctx.fillStyle = glowGrad;
    ctx.beginPath();
    ctx.arc(cx, cy, outerGlowRadius, 0, Math.PI * 2);
    ctx.fill();

    // Morphing sphere
    ctx.save();
    ctx.beginPath();
    const points = 32;
    for (let i = 0; i <= points; i++) {
      const theta = (i / points) * Math.PI * 2;
      const wave1 = Math.sin(theta * 3 + time * 2.4) * (energy * 18 * dpr);
      const wave2 = Math.cos(theta * 5 - time * 1.8) * (energy * 10 * dpr);
      const r = baseRadius + wave1 + wave2 + (Math.sin(time * 1.5) * 4 * dpr);
      const x = cx + Math.cos(theta) * r;
      const y = cy + Math.sin(theta) * r;
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    }
    ctx.closePath();

    const orbGrad = ctx.createRadialGradient(cx - baseRadius * 0.25, cy - baseRadius * 0.28, baseRadius * 0.08, cx, cy, baseRadius * 1.05);
    if (this.state === "error") {
      orbGrad.addColorStop(0, "#ff887b");
      orbGrad.addColorStop(1, "#991b1b");
    } else if (running && this.botSpeaking) {
      orbGrad.addColorStop(0, "#ffffff");
      orbGrad.addColorStop(0.35, `rgba(${Math.min(255, accent.r + 90)}, ${Math.min(255, accent.g + 80)}, 255, 0.95)`);
      orbGrad.addColorStop(1, `rgba(${accent.r}, ${accent.g}, ${accent.b}, 0.95)`);
    } else {
      const idlePulse = Math.sin(time * 1.2) * 0.08;
      orbGrad.addColorStop(0, `rgba(255, 255, 255, ${0.85 + idlePulse})`);
      orbGrad.addColorStop(0.4, `rgba(${accent.r}, ${accent.g}, ${accent.b}, 0.82)`);
      orbGrad.addColorStop(1, `rgba(${Math.max(0, accent.r - 40)}, ${Math.max(0, accent.g - 40)}, ${Math.max(0, accent.b - 20)}, 0.88)`);
    }

    ctx.fillStyle = orbGrad;
    ctx.shadowColor = `rgba(${accent.r}, ${accent.g}, ${accent.b}, ${0.5 + energy * 0.4})`;
    ctx.shadowBlur = (14 + energy * 26) * dpr;
    ctx.fill();
    ctx.restore();
  }

  render() {
    if (!this.shadowRoot) return;
    const running = ["connecting", "connected"].includes(this.state);
    const size = this.orbSize();
    const accentHex = this.accentHex();
    const accent = this.accentRgb();
    const status = this.statusLabel();
    const mode = this.visualMode();
    const camActive = this.cameraActive;
    const camEnabled = this.isCameraEnabled();

    this.shadowRoot.innerHTML = `
      <style>
        :host {
          display: flex;
          align-items: center;
          justify-content: center;
          width: 100%;
          padding: 12px 0;
          box-sizing: border-box;
        }
        .orb-card-container {
          position: relative;
          width: ${size}px;
          height: ${size}px;
          border-radius: 50%;
          background: var(--ha-card-background, var(--card-background-color, #ffffff));
          box-shadow: 0 14px 36px rgba(0, 0, 0, 0.12), 0 2px 8px rgba(0, 0, 0, 0.06);
          display: flex;
          flex-direction: column;
          align-items: center;
          justify-content: center;
          cursor: pointer;
          user-select: none;
          -webkit-tap-highlight-color: transparent;
          transition: transform 180ms cubic-bezier(0.2, 0, 0, 1), box-shadow 180ms ease;
          overflow: hidden;
          border: 1px solid var(--divider-color, rgba(127, 127, 127, 0.12));
        }
        .orb-card-container:hover {
          transform: scale(1.02);
          box-shadow: 0 18px 44px rgba(0, 0, 0, 0.18), 0 4px 12px rgba(0, 0, 0, 0.08);
        }
        .orb-card-container:active {
          transform: scale(0.97);
        }
        canvas.orb-canvas {
          position: absolute;
          inset: 0;
          width: 100%;
          height: 100%;
          pointer-events: none;
          z-index: 1;
        }
        .center-content {
          position: relative;
          z-index: 2;
          display: flex;
          flex-direction: column;
          align-items: center;
          justify-content: center;
          text-align: center;
          pointer-events: none;
          margin-top: ${mode === "avatar" ? "88px" : "0"};
          transition: margin 200ms ease;
        }
        .title {
          font-size: 14px;
          font-weight: 600;
          letter-spacing: 0.02em;
          color: var(--primary-text-color, #1f2937);
          margin-bottom: 2px;
          opacity: 0.88;
        }
        .status-text {
          font-size: 12px;
          font-weight: 500;
          color: var(--secondary-text-color, #6b7280);
          display: inline-flex;
          align-items: center;
          gap: 6px;
        }
        .status-text.pulse::after {
          content: "";
          display: inline-block;
          width: 6px;
          height: 6px;
          border-radius: 50%;
          background: ${accentHex};
          animation: orb-pulse 1.4s ease-in-out infinite;
        }
        @keyframes orb-pulse {
          0%, 100% { transform: scale(1); opacity: 0.5; }
          50% { transform: scale(1.6); opacity: 1; }
        }
        .floating-controls {
          position: absolute;
          top: 14px;
          right: 18px;
          z-index: 3;
          display: flex;
          align-items: center;
          gap: 8px;
          pointer-events: auto;
        }
        .icon-btn {
          background: rgba(127, 127, 127, 0.14);
          border: none;
          outline: none;
          border-radius: 999px;
          width: 28px;
          height: 28px;
          display: flex;
          align-items: center;
          justify-content: center;
          cursor: pointer;
          font-size: 12px;
          color: var(--primary-text-color, #333);
          transition: background 150ms ease, transform 150ms ease;
        }
        .icon-btn:hover {
          background: rgba(127, 127, 127, 0.25);
          transform: scale(1.08);
        }
        .icon-btn.active {
          background: ${accentHex};
          color: #ffffff;
        }
        .audio-prompt {
          position: absolute;
          bottom: 14px;
          z-index: 3;
          background: ${accentHex};
          color: #ffffff;
          padding: 4px 12px;
          border-radius: 999px;
          font-size: 11px;
          font-weight: 600;
          box-shadow: 0 4px 12px rgba(${accent.r}, ${accent.g}, ${accent.b}, 0.35);
          pointer-events: auto;
        }
        audio { display: none; }
      </style>
      <div class="orb-card-container" role="button" aria-label="${running ? "Stop Assistant" : "Talk with Assistant"}">
        <canvas class="orb-canvas"></canvas>

        <div class="floating-controls">
          ${camEnabled ? `
            <button class="icon-btn cam-btn ${camActive ? "active" : ""}" title="${camActive ? "Disable Camera" : "Enable Multimodal Camera"}">
              ${camActive ? "👁️" : "📷"}
            </button>
          ` : ""}
          <button class="icon-btn mode-btn" title="Cycle Visual Mode (Current: ${mode})">
            ✨
          </button>
        </div>

        <div class="center-content">
          <div class="title">${this.config.name || "Pipecat"}</div>
          <div class="status-text ${running ? "pulse" : ""}">${status}</div>
        </div>

        ${this.audioBlocked ? `<div class="audio-prompt">${this.t("enableAudio")}</div>` : ""}
        <audio autoplay playsinline></audio>
      </div>
    `;

    this.audio = this.shadowRoot.querySelector("audio");
    this.ensureVisualizer();
    this.attachAudio();

    const container = this.shadowRoot.querySelector(".orb-card-container");
    container.onclick = (e) => {
      e.stopPropagation();
      if (this.audioBlocked) {
        this.audioBlocked = false;
        this.attachAudio();
        this.render();
        return;
      }
      if (running) this.stop();
      else this.start();
    };

    const modeBtn = this.shadowRoot.querySelector(".mode-btn");
    if (modeBtn) modeBtn.onclick = (e) => this.cycleMode(e);

    const camBtn = this.shadowRoot.querySelector(".cam-btn");
    if (camBtn) camBtn.onclick = (e) => this.toggleCamera(e);
  }
}

if (!customElements.get("pipecat-live-orb-card")) {
  customElements.define("pipecat-live-orb-card", PipecatOrbCard);
}
if (!customElements.get("pipecat-orb-card")) {
  customElements.define("pipecat-orb-card", PipecatOrbCard);
}

window.customCards = Array.isArray(window.customCards) ? window.customCards : [];
if (!window.customCards.some((c) => c.type === "pipecat-live-orb-card")) {
  window.customCards.push({
    type: "pipecat-live-orb-card",
    name: "Pipecat Live Orb Card",
    description: "A circular tactile voice assistant with Expressive Avatar, 3D Quantum Blob, and Multimodal Camera Vision.",
    preview: true,
  });
}
