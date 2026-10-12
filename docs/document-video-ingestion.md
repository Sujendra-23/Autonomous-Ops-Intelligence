# Scanned documents and screen recordings

Until now the pipeline only read text: a pasted transcript, or audio run through Whisper. This adds two
visual inputs that end in the same place, the typed `ExtractionResult` (tasks, decisions, risks and
blockers, each with owner, due date, `source_quote` and `confidence`).

| Input | Endpoint | What happens |
| --- | --- | --- |
| Scanned agenda, minutes or action list (PDF, PNG, JPEG, WebP) | `POST /api/transcripts/upload-document` | Pages are rendered to PNG and sent to a vision model; the reply is validated against the existing Pydantic models. |
| Screen recording (mp4, webm) | `POST /api/transcripts/upload-recording` | ffmpeg samples keyframes, a vision model captions each one with its timestamp, and the captions become the extraction input. |

Both return the existing `TranscriptDetail` and persist through `ExtractionPipeline`, so dedup, task
updates, webhooks and integrations behave as they do for a transcript. Both are refused with `501` when
`AUTH_MODE=oidc`: SaaS mode defers extraction to a queue, and the vision call happens inline here.

```sh
curl -H "X-API-Key: $INGEST_API_KEY" \
  -F file=@agenda.pdf -F title="Platform sync" -F meeting_date=2026-03-02 \
  http://localhost:8000/api/transcripts/upload-document

curl -H "X-API-Key: $INGEST_API_KEY" \
  -F file=@demo.mp4 -F frame_mode=scene -F include_audio=false \
  http://localhost:8000/api/transcripts/upload-recording
```

## Document path

1. **Validate and render.** Extension allow-list, 20 MB cap, at most 10 pages, 40 MP decode ceiling.
   PDFs render with pdfium (`pypdfium2`, Apache-2.0) at 150 dpi; images are re-encoded to PNG. Edges are
   capped at 1568 px, which is where the providers downscale anyway.
2. **Extract.** `LLMClient.extract_images` is a new method on the existing provider adapter, so the
   provider is still chosen by `LLM_PROVIDER` (Claude by default, OpenAI supported). It uses the
   transcript system prompt plus a short addendum: quotes must be legible text copied from the page,
   table rows count as explicit assignments, page furniture is ignored.
3. **Validate.** The reply goes through the same `_parse_extraction`, so a malformed answer is an
   `invalid_output` outcome, not a half-written row.
4. **Persist.** `ExtractionPipeline.process(..., precomputed=result)` skips the text LLM call. The
   transcript row's `content` is the summary plus the verbatim `source_quote`s, which is what chunking
   and search index; the page images themselves are not stored.

Metrics reuse `aoi_llm_*` with `operation="extract_document"` (and `caption_frame` for video), plus
`aoi_ingest_document_pages_total` and `aoi_ingest_video_frames_total`. See
[observability.md](observability.md).

## Video path

`services/video_frames.py` runs ffmpeg with a `select` filter and reads each kept frame's `pts_time`
from `showinfo`:

- `frame_mode=scene` (default) keeps the first frame plus every frame whose scene score exceeds 0.3.
  If that yields fewer than two frames (a static screen) it falls back to one frame per
  `interval_seconds`.
- `frame_mode=interval` samples on a fixed cadence (default 30 s).
- At most 40 frames are kept, thinned evenly. Frames are scaled to 1280 px wide.

Each frame is captioned (four at a time); a frame that fails is skipped, and the request fails only if
none can be captioned. Captions are merged as `[mm:ss] caption` lines under a `## Screen captions`
heading. With `include_audio=true` the Whisper transcript is placed above them, so a decision spoken at
04:10 and the Jira board on screen at 04:12 reach the model together. Audio is off by default because
many screen recordings have none, and Whisper needs `OPENAI_API_KEY`.

Cost scales with frame count: one vision call per kept frame plus one text extraction.

## Eval

```sh
cd backend
python -m evals.build_documents      # regenerate the synthetic set (dev deps: reportlab)
python -m evals.run_documents        # offline, stub model
python -m evals.run_documents --live # real model through the configured provider (API usage)
```

`evals/documents/` holds 20 generated documents and `evals/documents.json` holds the expected fields.
Nothing in them is real: names, tasks and dates come from small word banks with a fixed seed. Layouts
are an action table, bulleted minutes and a two-page PDF; 5 are born-digital PDFs, 15 are raster
"scans" (rotation, blur, noise, greyscale PNG or JPEG), and two contain nothing to extract. In total
there are 54 tasks, 19 decisions and 13 risks.

The runner matches each predicted entity to a reference entity by fuzzy title (similarity at least 0.6)
and reports, per field:

- `tasks`, `decisions`, `risks`: precision and recall of the matched entities;
- `task.owner`, `task.due_date`: precision and recall, where an invented or wrong value is a false
  positive and a missing or wrong reference value (including on a task the model never found) is a
  false negative;
- `source_quote` grounding: the share of predicted quotes found in the document's text.

**What has been run.** Offline mode uses a stub that replays the reference answers after checking it
was handed the right number of PNG pages. It exercises rendering, the adapter-facing interface, schema
validation and the scorer, so it always reports 1.00 and says so. Unit tests check the scorer against
hand-computed counts using a deliberately wrong prediction. **`--live` has not been run**: no API key
was configured on the machine that built this, so there is no accuracy figure for any model. Run it with
your key before quoting a number; it makes 20 vision calls.

Limits of the set: it is synthetic and printed-text only (no handwriting, no photographs of whiteboards),
and the reference answers were written by the generator that also wrote the page, so it measures
reading and structuring clean-ish scans, not messy real minutes.

## Tests

`tests/test_documents.py` (rendering, limits, both provider adapters with stubbed SDKs, metrics, scorer),
`tests/test_video_frames.py` (a synthetic video built by ffmpeg: scene cuts, interval fallback,
thinning, caption failures) and `tests/test_ingest_endpoints.py` (both endpoints against Postgres with a
stub model). The first two run without infrastructure; the endpoint tests skip without Postgres like the
other pipeline tests. ffmpeg-dependent tests skip when ffmpeg is missing.
