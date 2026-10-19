# Pipecat Live Documentation

## Requirements

- Home Assistant with the Model Context Protocol Server integration enabled.
- An API key for the realtime model provider.
- A reachable LAN IP for Home Assistant if ESP32 satellites will connect.

## Configuration

Most settings live in the Pipecat Live web UI, not in Home Assistant add-on
options.

`runner_port`
: Pipecat runner port. Keep the default unless you also know how Home
Assistant ingress and direct clients reach the add-on.

`log_level`
: Application log level.

## Web UI

`Assistant`
: Start or stop the browser voice test for the active pipeline. The same active
pipeline is used by browser tests, ESP32 satellites, and the add-on runner.

`Pipelines`
: Add, duplicate, delete, and open complete Pipecat runtime profiles. Opening a
pipeline does not make it active; use **Set active** in the pipeline detail view
and save. After opening a pipeline, edit its colored steps from the canvas.
Pipecat Flow can be added only to composed realtime pipelines; speech-to-speech
profiles show it as unavailable.

`Pipecat Flow`
: For composed realtime pipelines, open the nested Flow view to edit a visual
Pipecat Flow stored in the same schema as the Pipecat Flows Editor. The view
includes filtered examples such as Minimal, Food Ordering, and Home Pizza via
MCP. The default Flow remains pass-through until enabled.

`Integrations`
: Configure Google and MCP providers, including Google Gemini Live, Google
Gemini Cloud, Google Cloud STT, Google Cloud TTS HTTP, Google Cloud TTS
Streaming, Google Imagen, Web Search, and Home Assistant MCP. Google Cloud TTS
HTTP is a normal one-shot synthesis option with a higher latency profile than
Google Cloud TTS Streaming. Home Assistant MCP shows Automatic, Manual, or
Error state and contains the MCP test/reset controls.

Gemini is split intentionally: **Google Gemini Live** is for native
speech-to-speech pipelines, while **Google Gemini Cloud** is for composed
pipelines that use separate STT, LLM, and TTS steps.

`Runtime`
: Tune short-lived session memory, cache the MCP tool schema, enable audio
debug captures, and inspect recent Home Assistant MCP calls made by the
assistant.

### Home Assistant MCP

In a normal Home Assistant add-on install, Pipecat Live uses the Supervisor
token provided by Home Assistant (`homeassistant_api: true`) to reach
`/api/mcp`. Open **Integrations > Home Assistant MCP** and select **Test MCP**.

Select **Automatic defaults** to clear a custom MCP URL or saved access token
and return to the Supervisor-backed defaults. The manual access-token field is
only for custom deployments where the Supervisor token is not available or a
custom MCP URL is used.

### Audio debug captures

Open **Runtime**, enable **Record audio in/out**, save, and run a
voice test or satellite session. The add-on stores separate input and output
WAV files under `/data/audio-debug` and shows download links in the Runtime
panel. Clear the captures after troubleshooting if they include private audio.

### Home Assistant MCP call history

Open **Runtime > Home Assistant actions** to inspect the recent MCP tools called
by the assistant. The history is in-memory, capped to recent calls, and intended
for debugging what the assistant attempted to do in Home Assistant.

### Session Memory and Web Search pipeline steps

Short-lived Session Memory is enabled by default and appears as a pipeline
step. It keeps the last few messages for a browser or satellite client for a
limited time, so reconnecting does not immediately lose the conversational
context. It is in-memory only and is cleared when the add-on restarts.

Web Search is also a pipeline step. Configure **Integrations > Web Search**
(which uses **Google Gemini Cloud** with Google Search grounding), then add or
enable the Web Search step in a pipeline. Enable **Announce web search** on the
step if the assistant should say "Please hold, I'm checking." before using a
search tool. Home Assistant device control still uses MCP tools.

### Composed realtime latency

Composed realtime pipelines stream audio through STT, LLM, and TTS stages. STT
must still produce text before the LLM can act on it, but Google Cloud TTS
Streaming can synthesize streamed LLM output in chunks. In **Integrations**,
**Google Cloud TTS Streaming** exposes a **TTS streaming** setting. `Sentence
chunks` is smoother; `Token chunks` can reduce latency when partial text is
handled well.

## Default Gemini Live setup

Gemini Live is the first-run speech-to-speech pipeline. It receives audio from
SmallWebRTC and returns audio directly, while Home Assistant device control is
handled through MCP tools.

1. In Home Assistant, enable **Model Context Protocol Server**.
2. Start Pipecat Assist and open the web UI.
3. Open **Integrations > Home Assistant MCP** and select **Test MCP**. A
   healthy result shows the number of available tools. In a normal add-on
   install this uses the Supervisor token automatically.
4. Open **Integrations > Google Gemini Live**:
   - Paste a Google AI Studio API key.
   - Keep `gemini-3.8-live` as the realtime model.
   - Use a Gemini Live voice such as `Charon` or `Puck`.
5. Open **Pipelines**, then open **Gemini Live Home Assistant**.
6. Keep the default instructions or adapt them to the household.
7. Save the pipeline.

### Browser voice test

Open **Assistant**, select the Gemini Live pipeline, and choose **Start voice
test**. Allow microphone access, wait for **Connected**, then try:

- `What devices are available in the living room?`
- `Turn on the living room lamp.`
- `Set the living room lamp brightness to 30 percent.`

If the browser cannot access the microphone, open Home Assistant over HTTPS or
from a trusted local origin. If WebRTC connects but the assistant hears the
wrong text, use **Runtime > Record audio in/out** and inspect the captured WAV
files.

### Home Assistant Assist bridge

This verifies the custom Home Assistant entities, selected pipeline, and MCP
tools. It is not a full-duplex Gemini Live audio test; HA Assist still uses the
classic STT -> Conversation -> TTS path. For the lowest latency voice
experience, use the add-on Assistant page, ESPHome satellite, Pipecat ESP32, or
the Lovelace WebRTC card.

1. Copy or install `custom_components/pipecat_live` into Home Assistant.
2. Restart Home Assistant if the integration is not already available.
3. Add **Pipecat Live** from **Settings > Devices & services**.
4. Confirm the suggested add-on URL. In Home Assistant OS or Supervised
   installs, the integration asks Supervisor for the installed add-on and
   prefills the first reachable URL. Change it only if Core cannot reach the
   suggested address.
5. In **Settings > Voice assistants**, select **Pipecat Live** for
   Conversation, Speech-to-text, and Text-to-speech. Use the only available
   Pipecat Live language entry; the real language and voice are configured in
   the add-on.
6. Both Gemini Live and Google Gemini Cloud pipelines support the bridge.
7. Type or speak a Home Assistant request in Assist and check the add-on logs
   for MCP tool calls and model errors.

### Home Assistant AI Tasks image generation

Pipecat Live exposes the Home Assistant AI Task entity with generated-data
and image-generation support. For images:

1. Open **Integrations > Google Imagen**.
2. Enable the integration, enter your Google API key, and choose the image
   model.
3. Open **Runtime > Image task provider** and either keep **First enabled image
   provider** or select **Google Imagen**.
4. In Home Assistant AI Tasks or AI Actions, choose **Pipecat Live** for
   image generation.

### Lovelace WebRTC card

The custom component automatically registers the dashboard card module when the
Pipecat Live integration is installed. In the default Lovelace storage mode,
open a dashboard, select **Add card**, and choose **Pipecat Live** from
**Custom cards**.

If your Lovelace resources are managed in YAML mode, add the card module as a
resource and then add a manual card:

```yaml
lovelace:
  resources:
    - url: /pipecat_live/pipecat-live-card.js?v=0.1.79
      type: module
```

```yaml
type: custom:pipecat-live-card
name: Pipecat Live
animation_on_idle: true
compact_mode: false
accent_color: "#206cff"
audio_buffer_ms: 120
```

The card talks to Home Assistant at `/api/pipecat_live/config` and
`/api/pipecat_live/offer`. The custom component proxies those calls to the
add-on and keeps the add-on Ingress token out of dashboard YAML. The card uses
the active pipeline selected in the add-on. If you configure more than one
Pipecat Live integration entry, add `entry_id` to point the card at a
specific entry.

`animation_on_idle` keeps the visualizer moving while nobody is speaking.
Set it to `false` for a still idle card. `compact_mode` hides the transcript.
`accent_color` accepts a HEX color. `audio_buffer_ms` is a browser WebRTC
jitter-buffer hint; raising it can smooth choppy playback at the cost of a
little extra delay.

### Gemini troubleshooting

- `Missing module: google.genai`: the add-on image is too old.
- `model not found`: check Gemini Live access in Google AI Studio and the
  realtime model value in **Integrations > Google Gemini Live**.
- MCP or `401`: open **Integrations > Home Assistant MCP**, select
  **Automatic defaults**, restart the add-on, then select **Test MCP**.
- Voice `marin` does not work with Gemini: set the Gemini voice to `Charon` or
  another Gemini Live voice.
- Browser voice test has no microphone: use HTTPS or a trusted local origin.

## ESPHome satellite

Open **Runtime > ESPHome satellite** and copy the complete WebSocket endpoint.
The endpoint contains the satellite secret and selected pipeline. Replace its
host with the Home Assistant LAN address if the displayed Ingress host is not
reachable from the device, then store the URL in ESPHome `secrets.yaml`.

```yaml
external_components:
  - source: github://cs-gergely04/pipecat-live@main
    components: [va_pipecat]

va_pipecat:
  id: pipecat_va
  url: !secret pipecat_satellite_url
  microphone:
    microphone: processed_microphone
    channels: 0
  speaker: assistant_speaker
  barge_in: true
```

The endpoint uses PCM16 mono at 16 kHz from the device and PCM16 mono at 24 kHz
for assistant playback. Pipecat drives `listening`, `thinking`, `speaking`, and
terminal states. The device delays post-response transitions until its local
speaker buffers have drained.

Full setup, actions, triggers, and lifecycle:
[components/va_pipecat/README.md](../../components/va_pipecat/README.md).

## Standalone Pipecat ESP32

The SmallWebRTC endpoint remains available for standalone `pipecat-esp32`
firmware:

```bash
export PIPECAT_SMALLWEBRTC_URL="http://<ha-lan-ip>:7865/api/offer?token=<satellite-secret>"
```

## Home Assistant integration

Copy or install `custom_components/pipecat_live` from this repository, then
add the integration in Home Assistant. The add-on URL is auto-detected through
Supervisor when possible and remains editable for custom installations. The
integration provides Conversation, STT, TTS, and the Lovelace card asset.
