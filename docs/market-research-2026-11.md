# Meeting-product market review — November 12, 2026

This is a comparison of current vendor-published capabilities, not a customer survey
or proof of demand. The priorities below are product inferences for AOI's company
meeting-to-action workflow, preserving the user's preference for a simple interface.

| Signal from primary sources | AOI decision |
| --- | --- |
| [Fireflies](https://fireflies.ai/pricing), [Otter](https://otter.ai/pricing) and [Fathom](https://www.fathom.ai/pricing) list enterprise SSO; all three also list SCIM. | Complete the OIDC sign-in experience and test PKCE/logout. Keep SCIM as future work requiring identity lifecycle design. |
| Fireflies lists meeting search and transcript/summary downloads; Otter lists advanced search/export. | Add title/transcript search, status and time filters to Meeting notes, with server pagination, plus full transcript downloads. |
| Fathom lists summaries and post-call action items; its [follow-up workflow](https://www.fathom.ai/launchpad/follow-up-email-after-meeting) prepares a draft from conversation outcomes. | Add editable recaps and follow-up drafts from saved tasks/decisions/risks/blockers, with copy/download. No automatic sending or public sharing. These are deterministic outputs from saved records, not a new AI summarization model. |
| All three emphasize integrations and team knowledge. | Retain AOI's existing semantic search, task connectors, source evidence, roadmaps, multilingual transcription and company workspaces; avoid duplicate destinations. |

Shipped in this pass: explicit Continue with SSO and provider/scopes configuration,
direct authenticated header Logout, clear local-session wording, serialized OIDC
callbacks, provider/local logout handling, meeting-library filters, editable recap and
follow-up outputs, and transcript downloads. Setup is in [SSO](sso.md).

Next candidates: SCIM provisioning/deprovisioning, fine-grained meeting sharing,
collaborative comments, provider-generated summary templates and CRM-specific workflows.
These need customer validation and access/lifecycle requirements before implementation;
they are not shipped or implied by the SSO feature. No pricing changes were made.

Verification: synthetic-provider Chromium SSO tests, mocked-record UI copy/download and
filter checks, PostgreSQL search/count/pagination/literal-wildcard checks, and existing
JWT verifier tests. Native live identity-provider behavior, real customer demand,
external invitation delivery and model accuracy are outside this evidence.
