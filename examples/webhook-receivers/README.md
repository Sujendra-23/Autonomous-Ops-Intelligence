# Webhook receiver examples

Two small receivers for the signed webhooks that the backend sends (see
[Outbound webhooks](../../docs/integrations.md#outbound-webhooks)). Use them to try the feature,
or copy `verify` into your own service.

| | Run | Test |
| --- | --- | --- |
| Python (standard library only) | `WEBHOOK_SECRET=... python python/receiver.py 8081` | `pytest backend/tests/test_webhook_receivers.py` |
| Node 18+ (no dependencies) | `WEBHOOK_SECRET=... node node/receiver.mjs 8081` | `cd node && node --test` |

Point `WEBHOOK_URL` at a reachable **https** URL for the receiver (a tunnel works for local trials).
The backend refuses `http` destinations, and `WEBHOOK_SECRET` must be at least 32 characters and the
same on both sides.

## What the receivers check

1. `X-AOI-Signature` equals `sha256=` plus the hex HMAC-SHA256, keyed with the secret, of
   `X-AOI-Timestamp + "." + raw body`. The comparison is constant time.
2. The timestamp is within 5 minutes of now, which blocks replayed captures.
3. The signature covers the **raw bytes**. Parsing and re-serializing the JSON changes the bytes
   and breaks verification, so verify first and parse afterwards.
4. `X-AOI-Event-ID` is remembered, so a duplicate (delivery is at least once) returns `200` without
   running your handler again. The in-memory set is only a demo. In production use a unique
   database key on the event ID.
5. Your handler runs before the event is marked seen. If it raises, the receiver returns `500` and
   the backend retries. Return `2xx` quickly and do slow work in a queue, because the backend
   times out after 10 seconds.

Status codes: `200` accepted or duplicate, `400` malformed, `401` bad signature or stale timestamp,
`413` body over 1 MB, `500` handler failed. The backend retries `408`, `429`, `5xx` and network
errors, and treats any other `4xx` as permanent.

`vector.json` holds one real signed request produced by the backend's `signed_headers`. The backend
tests fail if the producer and this file drift apart, and both receivers are checked against it.
Events can arrive out of order after retries, so use `occurred_at` or refetch the task rather than
assuming order.
