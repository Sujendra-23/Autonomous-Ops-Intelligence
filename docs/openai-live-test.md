# OpenAI live-test result — October 4, 2026

A real OpenAI API test passed using the existing backend key and synthetic audio.
No meeting recording, microphone, or external integration was used.

- Live transcription: `gpt-4o-transcribe`, 24 kHz mono PCM16, paced 100 ms frames.
- Structured extraction: `gpt-4o-mini` through the existing OpenAI adapter.
- Audio duration: 9.38 seconds; 29 interim updates; first final transcript at 10.87 seconds.
- Full stream-through-extraction duration: 17.64 seconds (one measured run, not a benchmark).
- Task: Alex finishes the migration plan, due October 9, 2026.
- Decision: use a staged rollout.
- Blocker: infrastructure must grant database access.

[Full synthetic test output](openai-live-test-result.json)

The test found and fixed the older session configuration, incomplete interim text,
and final-audio flushing. It also exposed incorrect relative-date inference. Live meetings
now record a date, and extraction receives a computed calendar reference. The sample's
Friday deadline was verified; this does not guarantee every natural-language date is correct.

The adapter keeps the existing `gpt-4o-transcribe` model with server VAD, which was accepted
by the live service in this test. Other transcription models can require different turn
settings; do not assume changing only the model name is sufficient.

## Repeat the smoke test

Install `backend/requirements.txt` in your Python environment. From the repository root,
with `OPENAI_API_KEY` configured in the environment or `.env`, generate a synthetic fixture
on macOS and run:

```sh
say -v Samantha -r 155 -o /tmp/aoi-live.aiff \
  'Alex will finish the migration plan by Friday. We decided to use a staged rollout. The launch is blocked until the infrastructure team grants database access.'
ffmpeg -y -i /tmp/aoi-live.aiff -ar 24000 -ac 1 -f s16le /tmp/aoi-live.pcm
python scripts/test_openai_live.py /tmp/aoi-live.pcm \
  --meeting-date 2026-10-04T09:00:00-07:00 \
  --output /tmp/aoi-live-result.json
```

This makes billable OpenAI transcription and extraction calls. Audio is capped at 30 seconds.
Keys are read server-side and are excluded from the output. It does not change `.env` or
send notifications, create external issues, or publish webhook events.

For normal app operation, set `LLM_PROVIDER=openai` and `STT_PROVIDER=openai`, then recreate
backend and worker with `make up`. The existing configured OpenAI key can serve both.

Scope: this verifies the backend provider adapters against OpenAI. It does not verify Chrome
tab capture, PostgreSQL persistence, or the complete browser-to-database route. The local
suite passes 61 tests; 11 database-dependent tests were skipped with Postgres unavailable.

Protocol reference: [OpenAI Realtime transcription](https://developers.openai.com/api/docs/guides/realtime-transcription).
