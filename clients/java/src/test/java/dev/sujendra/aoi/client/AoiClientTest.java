package dev.sujendra.aoi.client;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import dev.sujendra.aoi.client.Models.SubmitTranscript;
import dev.sujendra.aoi.client.Models.TaskList;
import dev.sujendra.aoi.client.Models.TaskQuery;
import dev.sujendra.aoi.client.Models.TranscriptDetail;
import java.io.IOException;
import java.time.Duration;
import java.time.Instant;
import java.util.UUID;
import okhttp3.mockwebserver.MockResponse;
import okhttp3.mockwebserver.MockWebServer;
import okhttp3.mockwebserver.RecordedRequest;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

class AoiClientTest {

    private static final String TRANSCRIPT_ID = "6f1f3b34-76a9-4b2a-9d3b-0b5a4a4c2a10";

    /** Shaped like the real TranscriptDetail, including extra fields the client must ignore. */
    private static final String COMPLETED =
            """
            {"id":"%s","title":"Sprint sync","status":"completed","source":"upload",
             "project_id":"11111111-1111-1111-1111-111111111111",
             "meeting_date":null,"processed_at":"2026-03-02T10:15:30.123456",
             "created_at":"2026-03-02T10:15:00","calendar_context":null,"content":"...",
             "participants":["Priya"],"error":null,
             "tasks":[{"id":"22222222-2222-2222-2222-222222222222","title":"Rotate credentials",
                       "owner":"Priya","due_date":"2026-03-14T00:00:00Z","status":"open",
                       "priority":"high","source_quote":"Priya will rotate them by Friday.",
                       "confidence":0.93}],
             "decisions":[{"id":"33333333-3333-3333-3333-333333333333","summary":"Ship weekly",
                           "rationale":null,"decided_by":["Priya","Marcus"],"source_quote":"ok"}],
             "risks":[],"blockers":[]}
            """
                    .formatted(TRANSCRIPT_ID);

    private final ObjectMapper json = new ObjectMapper();
    private MockWebServer server;

    @BeforeEach
    void start() throws IOException {
        server = new MockWebServer();
        server.start();
    }

    @AfterEach
    void stop() throws IOException {
        server.shutdown();
    }

    private AoiClient client(java.util.function.Consumer<AoiClientProperties> customise) {
        AoiClientProperties properties = new AoiClientProperties();
        properties.setBaseUrl(server.url("/").toString());
        properties.setReadTimeout(Duration.ofSeconds(5));
        customise.accept(properties);
        return new AoiClient(properties);
    }

    private static MockResponse jsonResponse(int status, String body) {
        return new MockResponse()
                .setResponseCode(status)
                .setHeader("Content-Type", "application/json")
                .setBody(body);
    }

    @Test
    void submitTranscriptPostsSnakeCaseJsonAndParsesTheExtraction() throws Exception {
        server.enqueue(jsonResponse(201, COMPLETED));

        TranscriptDetail detail =
                client(p -> p.setApiKey("ingest-key"))
                        .submitTranscript(
                                new SubmitTranscript(
                                        "Sprint sync",
                                        "Priya will rotate the credentials by Friday.",
                                        null,
                                        Instant.parse("2026-03-02T09:00:00Z"),
                                        java.util.List.of("Priya", "Marcus"),
                                        "Platform",
                                        null));

        RecordedRequest request = server.takeRequest();
        assertThat(request.getMethod()).isEqualTo("POST");
        assertThat(request.getPath()).isEqualTo("/api/transcripts");
        assertThat(request.getHeader("X-API-Key")).isEqualTo("ingest-key");
        assertThat(request.getHeader("Authorization")).isNull();
        assertThat(request.getHeader("Content-Type")).startsWith("application/json");
        // Regression: an h2c upgrade offer makes uvicorn answer 400 (found against the real API).
        assertThat(request.getHeader("Upgrade")).isNull();
        assertThat(request.getHeader("HTTP2-Settings")).isNull();
        JsonNode body = json.readTree(request.getBody().readUtf8());
        assertThat(body.get("title").asText()).isEqualTo("Sprint sync");
        assertThat(body.get("project_hint").asText()).isEqualTo("Platform");
        assertThat(body.get("meeting_date").asText()).isEqualTo("2026-03-02T09:00:00Z");
        assertThat(body.has("source")).as("null fields are omitted").isFalse();
        assertThat(body.has("sync_extract")).isFalse();

        assertThat(detail.status()).isEqualTo("completed");
        assertThat(detail.isFinished()).isTrue();
        assertThat(detail.tasks()).hasSize(1);
        var task = detail.tasks().get(0);
        assertThat(task.owner()).isEqualTo("Priya");
        assertThat(task.priority()).isEqualTo("high");
        assertThat(task.confidence()).isEqualTo(0.93);
        assertThat(task.dueDate()).isEqualTo(Instant.parse("2026-03-14T00:00:00Z"));
        assertThat(detail.decisions().get(0).decidedBy()).containsExactly("Priya", "Marcus");
        assertThat(detail.processedAt()).isEqualTo(Instant.parse("2026-03-02T10:15:30.123456Z"));
    }

    @Test
    void queuedSubmissionSendsSyncExtractFalse() throws Exception {
        server.enqueue(
                jsonResponse(201, "{\"id\":\"" + TRANSCRIPT_ID + "\",\"title\":\"t\",\"status\":\"pending\"}"));

        TranscriptDetail detail =
                client(p -> {}).submitTranscript(SubmitTranscript.of("t", "x".repeat(30)).queued());

        JsonNode body = json.readTree(server.takeRequest().getBody().readUtf8());
        assertThat(body.get("sync_extract").asBoolean()).isFalse();
        assertThat(detail.isFinished()).isFalse();
        assertThat(detail.tasks()).as("missing lists become empty").isEmpty();
    }

    @Test
    void bearerTokenAndWorkspaceAreSentAsHeaders() throws Exception {
        server.enqueue(jsonResponse(200, "{\"items\":[],\"total\":0}"));

        client(
                        p -> {
                            p.setBearerToken("tok-123");
                            p.setWorkspaceId("ws-9");
                        })
                .listTasks(TaskQuery.all());

        RecordedRequest request = server.takeRequest();
        assertThat(request.getHeader("Authorization")).isEqualTo("Bearer tok-123");
        assertThat(request.getHeader("X-Workspace-Id")).isEqualTo("ws-9");
        assertThat(request.getHeader("X-API-Key")).isNull();
    }

    @Test
    void listTasksSendsOnlyTheFiltersThatAreSet() throws Exception {
        server.enqueue(
                jsonResponse(
                        200,
                        """
                        {"items":[{"id":"22222222-2222-2222-2222-222222222222","title":"A",
                                   "owner":"Priya","due_date":"2026-03-14T09:30:00.5","status":"open",
                                   "priority":"medium","confidence":null,"sync_pending":false}],
                         "total":7}
                        """));

        TaskList tasks =
                client(p -> {})
                        .listTasks(TaskQuery.all().withStatus("open").withOwner("Priya O'Neil").onlyOverdue().withPage(25, 50));

        RecordedRequest request = server.takeRequest();
        assertThat(request.getRequestUrl().encodedPath()).isEqualTo("/api/tasks");
        assertThat(request.getRequestUrl().queryParameter("status")).isEqualTo("open");
        assertThat(request.getRequestUrl().queryParameter("owner")).isEqualTo("Priya O'Neil");
        assertThat(request.getRequestUrl().queryParameter("overdue")).isEqualTo("true");
        assertThat(request.getRequestUrl().queryParameter("limit")).isEqualTo("25");
        assertThat(request.getRequestUrl().queryParameter("offset")).isEqualTo("50");
        assertThat(request.getRequestUrl().queryParameter("project_id")).isNull();
        assertThat(tasks.total()).isEqualTo(7);
        assertThat(tasks.items().get(0).dueDate()).isEqualTo(Instant.parse("2026-03-14T09:30:00.5Z"));
    }

    @Test
    void emptyTaskQuerySendsNoQueryString() throws Exception {
        server.enqueue(jsonResponse(200, "{\"items\":[],\"total\":0}"));
        client(p -> {}).listTasks(TaskQuery.all());
        assertThat(server.takeRequest().getPath()).isEqualTo("/api/tasks");
    }

    @Test
    void unauthorizedSurfacesTheApiDetail() {
        server.enqueue(jsonResponse(401, "{\"detail\":\"invalid or missing X-API-Key\"}"));

        assertThatThrownBy(() -> client(p -> {}).getTranscript(UUID.fromString(TRANSCRIPT_ID)))
                .isInstanceOfSatisfying(
                        AoiApiException.class,
                        e -> {
                            assertThat(e.getStatus()).isEqualTo(401);
                            assertThat(e).hasMessageContaining("invalid or missing X-API-Key");
                        });
    }

    @Test
    void validationErrorsKeepTheFieldList() {
        server.enqueue(
                jsonResponse(
                        422,
                        "{\"detail\":[{\"loc\":[\"body\",\"content\"],\"msg\":\"String should have at least 20 characters\"}]}"));

        assertThatThrownBy(() -> client(p -> {}).submitTranscript(SubmitTranscript.of("t", "short")))
                .isInstanceOfSatisfying(
                        AoiApiException.class,
                        e -> {
                            assertThat(e.getStatus()).isEqualTo(422);
                            assertThat(e).hasMessageContaining("at least 20 characters");
                        });
    }

    @Test
    void nonJsonErrorBodiesAreReportedVerbatim() {
        server.enqueue(new MockResponse().setResponseCode(502).setBody("upstream down"));

        assertThatThrownBy(() -> client(p -> {}).getTask(UUID.randomUUID()))
                .isInstanceOfSatisfying(
                        AoiApiException.class,
                        e -> {
                            assertThat(e.getStatus()).isEqualTo(502);
                            assertThat(e).hasMessageContaining("upstream down");
                        });
    }

    @Test
    void connectionFailuresAreWrapped() throws IOException {
        AoiClient client = client(p -> {});
        server.shutdown();

        assertThatThrownBy(() -> client.listTasks(TaskQuery.all()))
                .isInstanceOfSatisfying(
                        AoiApiException.class, e -> assertThat(e.getStatus()).isEqualTo(-1));
    }

    @Test
    void awaitTranscriptPollsUntilTheWorkerFinishes() throws Exception {
        for (String status : new String[] {"pending", "extracting"}) {
            server.enqueue(
                    jsonResponse(
                            200,
                            "{\"id\":\"%s\",\"title\":\"t\",\"status\":\"%s\"}".formatted(TRANSCRIPT_ID, status)));
        }
        server.enqueue(jsonResponse(200, COMPLETED));

        TranscriptDetail detail =
                client(p -> {})
                        .awaitTranscript(
                                UUID.fromString(TRANSCRIPT_ID), Duration.ofSeconds(5), Duration.ofMillis(10));

        assertThat(detail.status()).isEqualTo("completed");
        assertThat(detail.tasks()).hasSize(1);
        assertThat(server.getRequestCount()).isEqualTo(3);
    }

    @Test
    void awaitTranscriptReturnsFailedTranscriptsInsteadOfWaiting() {
        server.enqueue(
                jsonResponse(
                        200,
                        "{\"id\":\"%s\",\"title\":\"t\",\"status\":\"failed\",\"error\":\"LLM error\"}"
                                .formatted(TRANSCRIPT_ID)));

        TranscriptDetail detail =
                client(p -> {})
                        .awaitTranscript(
                                UUID.fromString(TRANSCRIPT_ID), Duration.ofSeconds(5), Duration.ofMillis(10));

        assertThat(detail.status()).isEqualTo("failed");
        assertThat(detail.error()).isEqualTo("LLM error");
    }

    @Test
    void awaitTranscriptTimesOut() {
        for (int i = 0; i < 20; i++) {
            server.enqueue(
                    jsonResponse(
                            200,
                            "{\"id\":\"%s\",\"title\":\"t\",\"status\":\"extracting\"}".formatted(TRANSCRIPT_ID)));
        }

        assertThatThrownBy(
                        () ->
                                client(p -> {})
                                        .awaitTranscript(
                                                UUID.fromString(TRANSCRIPT_ID),
                                                Duration.ofMillis(60),
                                                Duration.ofMillis(20)))
                .isInstanceOfSatisfying(
                        AoiApiException.class, e -> assertThat(e.getStatus()).isEqualTo(408));
    }

    @Test
    void baseUrlIsRequired() {
        assertThatThrownBy(() -> new AoiClient(new AoiClientProperties()))
                .isInstanceOf(NullPointerException.class)
                .hasMessageContaining("base-url");
    }
}
