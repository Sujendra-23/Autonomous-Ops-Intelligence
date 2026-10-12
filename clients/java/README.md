# aoi-client (Java)

A small Java 17 client for the extraction API: post a transcript, read back the tasks, decisions and
risks. It is a plain library with Spring Boot 3 auto-configuration (`RestClient` underneath), not an
application.

```java
AoiClientProperties props = new AoiClientProperties();
props.setBaseUrl("http://localhost:8000");
props.setApiKey(System.getenv("AOI_API_KEY"));       // X-API-Key (development mode)
// props.setBearerToken(...); props.setWorkspaceId(...);  // OIDC / SaaS mode

AoiClient client = new AoiClient(props);

TranscriptDetail meeting = client.submitTranscript(
    SubmitTranscript.of("Sprint sync", "Priya will rotate the staging keys by Friday."));
meeting.tasks().forEach(t -> System.out.println(t.owner() + ": " + t.title()));

TaskList overdue = client.listTasks(TaskQuery.all().withStatus("open").onlyOverdue());
```

In a Spring Boot app, set `aoi.client.base-url` (plus `api-key`, `bearer-token`, `workspace-id`,
`connect-timeout`, `read-timeout`) and inject `AoiClient`; define your own `AoiClient` bean to override it.

| Method | Calls |
| --- | --- |
| `submitTranscript(SubmitTranscript)` | `POST /api/transcripts` (extracts before answering by default; `.queued()` returns at once) |
| `getTranscript(UUID)` | `GET /api/transcripts/{id}` |
| `awaitTranscript(UUID, timeout, pollInterval)` | polls `getTranscript` until `completed` or `failed` |
| `listTasks(TaskQuery)` | `GET /api/tasks` (status, owner, project, overdue, paging) |
| `getTask(UUID)` | `GET /api/tasks/{id}` |

Non-2xx responses and connection failures raise `AoiApiException` (`getStatus()` is `-1` when no
response arrived, `408` when `awaitTranscript` times out). The message includes the API's `detail`.
There are no automatic retries: `POST /api/transcripts` is not idempotent and a retry could extract the
same meeting twice.

## Build and test

```sh
cd clients/java
./mvnw verify            # JDK 17+; the wrapper downloads Maven
```

The tests use MockWebServer (no network) and an `ApplicationContextRunner` for the auto-configuration.
`LiveApiIT` is skipped unless `AOI_BASE_URL` is set; with a running API it round-trips a queued
transcript (no LLM key needed on the server):

```sh
AOI_BASE_URL=http://localhost:8000 AOI_API_KEY=... ./mvnw test -Dtest=LiveApiIT
```

## Scope

Transcript submission, transcript lookup and task reads only. File, document and recording uploads,
task updates (`PATCH`), projects, decisions listing and the live WebSocket are not covered.
