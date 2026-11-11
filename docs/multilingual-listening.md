# Multilingual listening

Live captures now default to **Auto-detect / mixed languages**. The extension also
provides a listening language menu for meetings mainly in one language. These are
transcription preferences, not translations or interface-language settings.

## Use it

1. Reload the unpacked Chrome extension after updating its files.
2. Choose **Listening language** above the recorder. Leave automatic mode selected
   for mixed-language speech, or choose a primary language such as Nepali, Hindi,
   Arabic, Spanish, Tamil, or French.
3. Start capture. Stop before changing the language. The selected preference is
   saved on the meeting and reused when the audio connection reconnects.
4. Open the meeting in the dashboard to see its saved listening preference and
   transcript. Transcript and evidence text support automatic text direction.

Apply migration `0008_multilingual_listening` (`alembic upgrade head`) and restart
the backend if it is not running with reload enabled. Existing records receive
`auto`; their transcript text is unchanged.

## Provider behavior

- **OpenAI:** the existing configured `gpt-4o-transcribe` model is retained.
  Automatic mode omits the language hint; a specific choice sends the ISO code
  in `audio.input.transcription.language`. The app never sends the strings
  `auto` or `multi` to OpenAI. Automatic mode supplies multilingual context and
  requests original scripts without translation. This change
  does not migrate the app to another OpenAI model or promise equal accuracy in
  every language.
- **Deepgram:** live transcription now uses Nova-3. Automatic mode sends
  `model=nova-3&language=multi`, enabling detection and code-switching within
  the provider's multilingual language set. A specific language sends its code
  instead. The multilingual set differs from the explicit-language set:
  **Nepali and Urdu require an explicit choice with this provider**. Do not
  assume every language offered in the menu is supported by Deepgram's auto mode.
- **No provider/key:** capture plumbing can connect, but it cannot generate
  speech transcripts. Configure STT on the backend first.

The extension loads its choices and provider guidance from authenticated
`GET /api/live/languages`. `POST /api/live/sessions` accepts optional `language`;
if omitted, the server uses `LIVE_TRANSCRIPTION_LANGUAGE` (default `auto`).
Invalid codes are rejected before a session is created. The session response and
WebSocket ready message report the saved setting. Each capture has its own
provider instance; a company's language choice does not change another capture.

Structured extraction accepts multilingual and code-switched text, keeps source
quotes verbatim, and is instructed to keep each extracted title/summary in its
source passage's language. JSON property names and status enums retain the API
schema. No translation service or new LLM call is introduced.

## Verification and limits

A real OpenAI streaming test on October 24, 2026 passed with a 9.87-second
synthetic English/Spanish/Hindi recording in automatic mode, preserving English,
Spanish, and Devanagari text in one session. It used no meeting recording or
microphone audio. [Synthetic test result](multilingual-live-test.json).

Live verification exposed a finalization race: a delayed VAD commit could reset
the local byte counter after later audio was sent. Finalization now flushes when
any audio was sent and waits for the provider acknowledgment and queued final
transcripts before closing. A regression test covers the counter-reset case.
Fresh-database verification also fixed an earlier redaction migration's open
server-side cursors so subsequent transcript schema migrations can run.

Protocol tests verify provider payloads, automatic versus explicit language
configuration, Unicode preservation, input validation, per-connection preferences,
and persisted session settings. These tests and the single synthetic sample do
not establish live speech accuracy across all supported languages.
Evaluate your languages, accents, background noise, and language switching with
representative audio before relying on transcription quality.

Provider references checked for this implementation:

- [OpenAI input transcription fields](https://developers.openai.com/api/reference/resources/realtime/subresources/calls/methods/create)
- [Deepgram models and languages](https://developers.deepgram.com/docs/models-languages-overview)
- [Deepgram multilingual update](https://developers.deepgram.com/changelog/2026/10/23)
