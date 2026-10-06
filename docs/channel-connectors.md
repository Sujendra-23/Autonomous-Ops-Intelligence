# Discord and Microsoft Teams connectors

These outbound connectors publish meeting summaries after AOI processes a
transcript. Summaries include extracted tasks, decisions, risks, and blockers.
They do not import chat history or join meetings. Both can run alongside Slack
and Notion, and each is disabled until its webhook URL is configured.

## Discord

1. In the destination channel's settings, open **Integrations → Webhooks**.
2. Create a webhook and copy its URL.
3. Set `DISCORD_WEBHOOK_URL` in the backend `.env`.
4. Restart the API backend and any worker processes using that configuration.
5. In the extension's **Connectors** tab, choose **Check status**.

AOI uses `wait=true` to confirm that Discord created a message. Mentions are
turned off, including `@everyone`, role mentions, and user mentions. Content is
bounded to Discord's 2,000-character limit; longer summaries direct readers to
AOI. Forum or media channels require a thread destination; add `thread_id` to the
webhook URL to target an existing thread.

[Discord webhook API](https://docs.discord.com/developers/resources/webhook#execute-webhook)

## Microsoft Teams

1. In the destination channel, open **Workflows**.
2. Create a workflow using **When a Teams webhook request is received** and a
   step that posts the received Adaptive Card attachment to your channel. A
   template for posting webhook cards can provide this structure.
3. Set the trigger's caller authentication to **Anyone**. AOI uses the secret
   webhook URL and does not supply a Microsoft OAuth bearer token; triggers
   limited to your tenant or specific users need a separate token integration.
4. Copy the generated URL into `TEAMS_WEBHOOK_URL` in the backend `.env`.
5. Restart the backend and workers, then choose **Check status** in the panel.

The request body has `type: message` and an `attachments` array containing an
`application/vnd.microsoft.card.adaptive` card. Workflows expecting a custom
`text` body require adjustment to accept this attachment format. Assign a
co-owner to keep the workflow manageable if its creator leaves the organization.

[Microsoft webhook setup](https://learn.microsoft.com/en-us/microsoftteams/platform/webhooks-and-connectors/how-to/add-incoming-webhook)
[Teams webhook trigger](https://learn.microsoft.com/en-us/connectors/teams/#when-a-teams-webhook-request-is-received)

## Delivery and configuration status

The panel's **Configured** label reports server settings, not a live provider
access test. Completed processing sends a summary automatically to each enabled
connector; there is no test-message action. Delivery follows AOI's existing
best-effort notifier behavior. Failed notifications do not roll back extracted
notes and are not retried by the generic signed-webhook outbox.

Webhook URLs contain credentials. Keep them in server configuration. Provider
errors report only an HTTP status or a generic network failure, without logging
the URL or response body. HTTP redirects are not followed.
