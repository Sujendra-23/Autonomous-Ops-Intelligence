# Transcription review direction

Mode: Operate. Help the user inspect provider-flagged passages before relying on
meeting names, dates, amounts and commitments, without hiding the full transcript.

## First viewport

Retain the existing meeting-detail shell, editorial meeting title, capture-source
and listening-language metadata, and Notes / Transcript tabs. Within Transcript,
keep **Copy transcript** adjacent to its heading. When stored segments contain
review flags, show an expandable **Review uncertain passages (count)** area above
the complete transcript. Its ochre text, quiet warning surface and fine divider
inherit the incumbent state vocabulary. Passages wrap on narrow screens and retain
automatic text direction for multilingual content.

## Interaction and states

The review disclosure lists the flagged source passages and explains the next
action: check names, dates and commitments against the audio. It is a review cue,
not an accuracy guarantee for other words. The full transcript remains separately
available and is the source for copying. Copy success/failure uses explicit status
feedback. If no transcript text exists, preserve the existing honest empty state.
If no segments are flagged, do not invent a review item or imply perfect certainty.
The desktop live preview can similarly point out that some words need review.

## Product constraints

Use the configured speech provider and preserve the listening language and bounded
names/technical-term hints across reconnects. Retain source evidence in the language
spoken, workspace boundaries and existing redaction. Confidence is a heuristic;
provider scores are not calibrated against each other and unavailable confidence
remains unknown. Do not rewrite uncertain content into invented facts or promise
zero errors. Audio-source guidance, lossless local backup, ordered delivery and
final-result flushing support quality but do not establish an accuracy target.

Existing Talk to AOI commands, field dictation and optional spoken replies are
unchanged. They are separate from call recording and transcript-confidence review.

## Constraints and evidence

Inherit DESIGN.md and .impeccable/design.json unchanged. This is a local warning
disclosure in the approved cream/charcoal/lavender workspace, with Figtree reading
and control text and the existing EB Garamond page heading. It adds no global
tokens or replacement visual identity. No global design drift was repaired.

Reviewed source: frontend/src/components/Meetings.tsx, studio.css,
desktop/ui/renderer.js, docs/transcription-quality.md and
docs/transcription-evaluation.json. Desktop and mobile visual evidence is stored
as .impeccable/review/transcript-review-desktop.png and
transcript-review-mobile.png, using explicitly labeled example content. Both
were included in the seven-screen finish review.

The recorded live-provider evaluation is one 12.31-second synthetic English fixture
with OpenAI gpt-4o-transcribe. Critical facts were preserved, including the final
negative commitment. Its raw case/punctuation-normalized WER is 0.2069 because
spoken numeric/date reference forms differ from formatted output. Retain that
metric and limitation alongside the critical-fact checks. This result does not
establish real-call, multilingual, noisy-room or Windows-capture accuracy. See
docs/transcription-quality.md for provider behavior, migration and evaluation scope.
