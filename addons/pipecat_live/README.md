<p align="center">
  <img src="https://raw.githubusercontent.com/cs-gergely04/pipecat-gemini/main/addons/pipecat_live/logo.png" alt="Pipecat Live" width="320">
</p>

# Pipecat Live

Pipecat Live runs a realtime Pipecat voice agent inside Home Assistant. It
connects to Home Assistant MCP for device control, serves a web UI through
Ingress, exposes WebRTC and ESPHome satellite transports, and backs Home
Assistant AI Tasks for generated data and image generation.

Open the web UI after starting the add-on. The first screen is the voice
assistant test surface. Pipelines are complete runtime profiles used by the UI,
ESPHome and standalone Pipecat ESP32 satellites, and Home Assistant cards.

Gemini Live is preconfigured as the default speech-to-speech profile. The UI
also includes a composed realtime profile (`Google STT + Gemini + Google TTS`)
and custom pipelines built from Google Gemini Cloud, Google Cloud STT, and
Google Cloud TTS (HTTP or Streaming). Provider settings are configured in
**Integrations**: Google Gemini Live, Google Gemini Cloud, Google Cloud STT,
Google Cloud TTS, Web Search (powered by Gemini Google Search grounding),
Google Imagen (for Home Assistant image-generation tasks), and Home Assistant
MCP are kept separate. Session Memory and Web Search are visible pipeline
steps, and official Pipecat Flows can be enabled inside composed realtime
pipelines.

The built-in Home Assistant MCP integration uses the Supervisor connection
automatically. The optional HA MCP Server Add-on integration is also detected
automatically from the add-on's generated secret URL and does not need a Bearer
token.

For setup, testing, and troubleshooting, see `DOCS.md`.
