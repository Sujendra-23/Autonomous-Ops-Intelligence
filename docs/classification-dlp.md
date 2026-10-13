# Transcript classification and DLP

Every text, audio/video upload and live transcript receives `sensitivity_label`,
`dlp_findings` and `dlp_policy_version` in transcript list/detail responses.
JSON ingestion and live session creation accept `sensitivity_label`; multipart
uploads accept it as a form field. The default is `internal`.

| Label | Policy |
| --- | --- |
| `public` | Explicitly requested and no PII or confidential marker detected |
| `internal` | Default for ordinary business text |
| `confidential` | Any supported PII, a confidential/restricted/trade-secret marker, or explicitly requested |

Labels only rise on subsequent writes, including live incremental extraction.
Recognized redaction placeholders also imply `confidential`, so already-sanitized
model output retains its sensitivity when ingested.
An explicit `public` request cannot override detected PII. A label expresses
sensitivity; it does not change workspace permissions or prevent publishing a
sanitized summary through an enabled connector.

## Detection and redaction

The deterministic detector in `backend/app/services/dlp.py` covers:

| Type | Validation |
| --- | --- |
| Email | ASCII email pattern, including plus addresses and subdomains |
| Phone | North American numbers and international `+` numbers of 8–15 digits; optional extension |
| Payment card | 13–19 digits, optional spaces/hyphens, valid Luhn checksum; rejects repeated-digit placeholders |
| US SSN | `AAA-GG-SSSS` pattern; rejects forbidden/zero components |
| Aadhaar | 12 digits starting 2–9, optional spaces/hyphens, valid Verhoeff checksum |
| Other ID | Explicit `passport`, `national id`, `tax id` or `driver's license` prefix with `:`, `=` or `#`; bounded alphanumeric identifier |

Emails, phone numbers and SSNs have no applicable checksum. Other explicitly
labeled IDs are conservatively redacted without claiming issuer validation.
Checksums validate a candidate's shape, not whether it is assigned to a person.
Invalid checksum candidates remain unchanged unless another supported pattern matches.
Unsupported international IDs, person names, addresses, Unicode email addresses,
spoken digit sequences and secrets require additional detectors. Regex-based DLP
cannot certify that text contains no sensitive information.

Matches become `[REDACTED:EMAIL]`, `[REDACTED:PHONE]`, `[REDACTED:CARD]`,
`[REDACTED:SSN]`, `[REDACTED:AADHAAR]` or `[REDACTED:ID]`. Overlapping matches
are resolved by longest span; redaction is idempotent. Findings retain types and
counts only, with no original values or reversible lookup. `dlp_findings` records
the maximum count seen per type in any text/metadata field, rather than a
cumulative count of live revisions.

ORM writes redact content, title, participants, calendar metadata and stored
errors before persistence, including asynchronous ingestion and live writes.
The full/incremental pipeline also applies the policy before embedding or
extraction. Final assembled prompts are redacted in both provider clients,
including prior context, and provider output is sanitized before persistence.
The OpenAI embedding boundary independently sanitizes text.

Jira and Slack filter customer-derived text at their HTTP boundaries, including
manually edited or legacy task content, fallback text, blocks and reminders.
Redaction precedes truncation. Linear, Notion, Discord/Teams and signed webhook
payloads also use the same filter. Credentials and destination identifiers are
kept separate from customer-derived text. Signing happens after redaction.

## Migration and existing data

Run `alembic upgrade head` with the deployment's schema-owner role. Migration
`0007_transcript_dlp` classifies and sanitizes existing transcript text and
metadata by workspace. It sanitizes old chunks and clears embeddings whose text
changed; reprocessing can rebuild those embeddings where enabled. Historical
task/decision rows remain available and are filtered at the outbound boundaries.

Redaction erases matched text. A downgrade removes metadata columns but cannot
recover original values. Test the migration on a disposable database before
deploying it to a large corpus; the backfill runs within the migration transaction.
Raw uploaded audio still reaches the speech-to-text provider before text exists;
this text policy does not redact audio or image pixels. Document/page and
recording-frame images still reach the vision provider; textual metadata and
model-generated text are filtered. Live interim text may be shown to the
authenticated participant before a finalized transcript write.

## Evaluation

```sh
cd backend
python -m evals.run_dlp
pytest tests/test_dlp.py -q
```

`backend/evals/dlp.json` contains 28 synthetic cases covering all labels,
supported types, invalid checksums, overlap, metadata-style email addresses,
dates/build numbers and existing placeholders. It is a regression set, not a
real-world accuracy estimate. CI checks exact labels and finding counts; boundary
tests inspect actual mocked SDK/HTTP payloads. PostgreSQL tests cover persistence,
label downgrade prevention and full/incremental extraction.
