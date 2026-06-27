# AOI Live Note-Taker (Chrome extension)

Captures the **active tab's audio** during any web meeting (Google Meet, Zoom
web, Teams web, a webinar, a recorded video…), streams it to your Autonomous
Operational Intelligence backend for real-time transcription, and shows
**tasks / decisions / risks / blockers as they're spoken** in a side panel.

The same extraction engine the batch pipeline uses runs incrementally here, so
notes are de-duplicated and updated live — a task mentioned twice stays one task,
and "that's done" closes it.

## How it works

```
 Meeting tab audio
   └─ service-worker.js   getMediaStreamId + create live session
        └─ offscreen.js   getUserMedia(tab) → 16 kHz AudioContext
              └─ pcm-worklet.js   Float32 → PCM16 (~128 ms chunks)
                    └─ WebSocket  /api/live/ws/{id}   (binary audio up)
                          └─ backend   Deepgram STT → incremental extraction
                    ◄──────────────   JSON notes/transcript (down)
        └─ sidepanel.js   renders transcript + live notes
```

Audio is relayed **through your backend**, so the STT provider key
(`DEEPGRAM_API_KEY`) stays server-side and never enters the browser.

## Backend prerequisites

In the backend `.env` — pick one STT provider:

```ini
# Option A — Deepgram
STT_PROVIDER=deepgram
DEEPGRAM_API_KEY=...           # https://console.deepgram.com/

# Option B — OpenAI Realtime (reuses OPENAI_API_KEY)
# STT_PROVIDER=openai
# OPENAI_REALTIME_MODEL=gpt-4o-transcribe

# plus an LLM key (ANTHROPIC_API_KEY or OPENAI_API_KEY) for extraction
```

The backend tells the extension which capture sample rate the chosen provider
expects (16 kHz for Deepgram, 24 kHz for OpenAI), so you don't configure it here.

Then `make up`. With `STT_PROVIDER=none` the WebSocket still connects but no
transcript is produced (useful to test the plumbing).

If `INGEST_API_KEY` is set on the backend, put the same value in the panel's
**API key** field.

## Load the extension

1. Open `chrome://extensions`, enable **Developer mode**.
2. **Load unpacked** → select this `extension/` folder.
3. Pin the extension and click it to open the side panel.

## Use it

1. Open/focus the meeting tab (it must be the **active** tab when you start).
2. In the side panel set the **Backend URL** (default `http://localhost:8000`),
   an optional **Project hint** (reuse the same hint across meetings to get
   cross-meeting continuity), and the **API key** if your backend requires one.
3. Click **Start capturing**. Speak / let the meeting run — transcript and notes
   populate live.
4. Click **Stop & finalize** to run the final pass (embeddings + Notion/Linear/
   Slack dispatch) and mark the meeting completed.

## Notes & limitations

- Tab capture mutes the tab's normal output, so the extension replays the audio
  through a 16 kHz context — meeting audio stays audible but is downsampled.
- One tab at a time. Capturing starts from the active tab.
- The `api_key` is passed as a WebSocket query parameter; fine for localhost,
  but terminate TLS (wss) if you expose the backend.
- If the socket drops mid-meeting the extension auto-reconnects with backoff and
  the backend resumes the same session from the persisted transcript.
