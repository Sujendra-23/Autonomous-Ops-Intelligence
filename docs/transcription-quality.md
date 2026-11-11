# Transcription quality and review

Meeting transcription uses the configured provider and model. This change retains
`gpt-4o-transcribe` for the existing OpenAI Realtime integration and Nova-3 for
Deepgram; it does not silently substitute an untested model. No transcription engine
can guarantee an error-free record of every voice, language, overlapping speaker,
background sound or poor connection.

Quality protections added:

- Direct system audio, an optional processed microphone and lossless local PCM/WAV
  capture in AOI Desktop. Audio-level, silence and clipping feedback help identify
  source problems before relying on notes.
- Per-meeting listening language and names/technical-term hints in both the desktop
  recorder and extension. Up to 50 bounded terms are preserved with the session and
  reused after reconnects. Existing DLP redaction applies to text and hints.
- OpenAI receives a faithful-transcription prompt, expected vocabulary, leading-audio
  padding and final-turn flushing. Compatible models can return token log probabilities.
- Deepgram receives key terms, punctuation and filler-word retention. Shutdown waits
  for its final results and metadata instead of closing immediately after CloseStream.
- Audio commit ordering, buffered reconnect frames, partial filesystem writes and
  the final partial AudioWorklet batch have regression tests. Audio is not silently
  dropped when a queue is exceeded: the recorder reports the problem and retains a
  local recording when enabled.
- Final transcript segments preserve provider confidence and review cues. The meeting
  Transcript view shows passages flagged for review; it keeps the full transcript
  separately. Confidence is a heuristic review aid, not the probability that a statement
  is correct or a guarantee that unflagged words have no errors. Missing confidence
  remains unknown, never synthesized as perfect certainty.

A segment is flagged when average provider confidence is below the review threshold,
a Deepgram word has low confidence, or an OpenAI token has especially low likelihood.
Scores are not calibrated across providers. Review names, dates, amounts and commitments
against the recording; automatic cleanup must not invent missing content.

Apply additive migration `0011_transcription_quality` and restart the backend. It
stores segment metadata on each existing workspace-scoped transcript. The existing
redaction policy protects segment text; transcripts and source evidence retain their
spoken language.

## Evidence

[The recorded evaluation result](transcription-evaluation.json) comes from a 12.31-second
synthetic English fixture streamed to the configured OpenAI provider. It preserved
Priya, Kubernetes, November 15th, the $12,500 budget, Sam's ownership and the final
negative commitment 'must not launch before security approval'. It is not a real-call,
multilingual, noisy-room or Windows capture benchmark.

The raw case/punctuation-normalized word error rate is 0.2069 because the reference
spells out 'fifteenth' and 'twelve thousand five hundred dollars', while the returned
transcript formats them as '15th' and '$12,500'. The report retains both strings and
states this limitation; it does not report that formatting as semantic loss, hide the
raw metric, or claim universal zero WER. Critical-fact checks are separately listed.

For your own consented test recordings, run `scripts/eval_transcription.py` with a
24 kHz mono PCM16 fixture and its exact reference. This makes a billable provider call;
it does not send audio unless explicitly run. Use representative accents, languages,
noise, overlapping speech and session lengths before setting an accuracy target.

Provider references: [OpenAI Realtime transcription](https://developers.openai.com/api/docs/guides/realtime-transcription),
[OpenAI session configuration](https://developers.openai.com/api/reference/resources/realtime/client-events),
[Deepgram streaming parameters](https://developers.deepgram.com/reference/speech-to-text/listen-streaming),
and [Deepgram final stream flush](https://developers.deepgram.com/docs/close-stream).
