package dev.sujendra.aoi.client;

import com.fasterxml.jackson.databind.DeserializationFeature;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.SerializationFeature;
import dev.sujendra.aoi.client.Models.SubmitTranscript;
import dev.sujendra.aoi.client.Models.TaskList;
import dev.sujendra.aoi.client.Models.TaskQuery;
import dev.sujendra.aoi.client.Models.TaskView;
import dev.sujendra.aoi.client.Models.TranscriptDetail;
import java.net.http.HttpClient;
import java.time.Duration;
import java.util.Objects;
import java.util.UUID;
import org.springframework.http.HttpStatusCode;
import org.springframework.http.MediaType;
import org.springframework.http.client.JdkClientHttpRequestFactory;
import org.springframework.http.converter.json.MappingJackson2HttpMessageConverter;
import org.springframework.web.client.RestClient;
import org.springframework.web.client.RestClientException;

/**
 * Blocking client for the extraction API: post a transcript, read back the extracted tasks.
 *
 * <p>Instances are thread-safe. Errors surface as {@link AoiApiException}.
 */
public final class AoiClient {

    /** Dates go out as ISO-8601 strings, and unknown response fields are ignored. */
    private static final ObjectMapper MAPPER =
            new ObjectMapper()
                    .findAndRegisterModules()
                    .disable(SerializationFeature.WRITE_DATES_AS_TIMESTAMPS)
                    .disable(DeserializationFeature.FAIL_ON_UNKNOWN_PROPERTIES);

    private final RestClient http;

    public AoiClient(AoiClientProperties properties) {
        Objects.requireNonNull(properties.getBaseUrl(), "aoi.client.base-url is required");
        // HTTP/1.1 only: the JDK client otherwise offers an h2c upgrade on cleartext requests, and
        // uvicorn (which serves the API) rejects an upgraded POST with "400 Invalid HTTP request".
        HttpClient jdk =
                HttpClient.newBuilder()
                        .version(HttpClient.Version.HTTP_1_1)
                        .connectTimeout(properties.getConnectTimeout())
                        .build();
        JdkClientHttpRequestFactory factory = new JdkClientHttpRequestFactory(jdk);
        factory.setReadTimeout(properties.getReadTimeout());

        RestClient.Builder builder =
                RestClient.builder()
                        .baseUrl(properties.getBaseUrl())
                        .requestFactory(factory)
                        .messageConverters(
                                converters -> {
                                    converters.clear();
                                    converters.add(new MappingJackson2HttpMessageConverter(MAPPER));
                                })
                        .defaultHeader("Accept", MediaType.APPLICATION_JSON_VALUE);
        if (hasText(properties.getApiKey())) {
            builder.defaultHeader("X-API-Key", properties.getApiKey());
        }
        if (hasText(properties.getBearerToken())) {
            builder.defaultHeader("Authorization", "Bearer " + properties.getBearerToken());
        }
        if (hasText(properties.getWorkspaceId())) {
            builder.defaultHeader("X-Workspace-Id", properties.getWorkspaceId());
        }
        this.http = builder.build();
    }

    /**
     * {@code POST /api/transcripts}. By default the API extracts before answering, so the returned
     * detail already contains the tasks; with {@link SubmitTranscript#queued()} it returns at once
     * and you can {@link #awaitTranscript wait} for the worker.
     */
    public TranscriptDetail submitTranscript(SubmitTranscript request) {
        return call(
                () ->
                        http.post()
                                .uri("/api/transcripts")
                                .contentType(MediaType.APPLICATION_JSON)
                                .body(request)
                                .retrieve()
                                .onStatus(HttpStatusCode::isError, AoiClient::fail)
                                .body(TranscriptDetail.class));
    }

    /** {@code GET /api/transcripts/{id}}. */
    public TranscriptDetail getTranscript(UUID id) {
        return call(
                () ->
                        http.get()
                                .uri("/api/transcripts/{id}", id)
                                .retrieve()
                                .onStatus(HttpStatusCode::isError, AoiClient::fail)
                                .body(TranscriptDetail.class));
    }

    /**
     * Polls {@link #getTranscript} until extraction is {@code completed} or {@code failed}.
     *
     * @throws AoiApiException with status {@code 408} if {@code timeout} passes first
     */
    public TranscriptDetail awaitTranscript(UUID id, Duration timeout, Duration pollInterval) {
        long deadline = System.nanoTime() + timeout.toNanos();
        while (true) {
            TranscriptDetail detail = getTranscript(id);
            if (detail.isFinished()) {
                return detail;
            }
            if (System.nanoTime() + pollInterval.toNanos() > deadline) {
                throw new AoiApiException(
                        408, "transcript " + id + " still " + detail.status() + " after " + timeout);
            }
            try {
                Thread.sleep(pollInterval.toMillis());
            } catch (InterruptedException interrupted) {
                Thread.currentThread().interrupt();
                throw new AoiApiException("interrupted while waiting for " + id, interrupted);
            }
        }
    }

    /** {@code GET /api/tasks} with the given filters. */
    public TaskList listTasks(TaskQuery query) {
        return call(
                () ->
                        http.get()
                                .uri(
                                        uriBuilder -> {
                                            uriBuilder.path("/api/tasks");
                                            if (query.status() != null) {
                                                uriBuilder.queryParam("status", query.status());
                                            }
                                            if (query.projectId() != null) {
                                                uriBuilder.queryParam("project_id", query.projectId());
                                            }
                                            if (query.owner() != null) {
                                                uriBuilder.queryParam("owner", query.owner());
                                            }
                                            if (query.overdue()) {
                                                uriBuilder.queryParam("overdue", true);
                                            }
                                            if (query.limit() != null) {
                                                uriBuilder.queryParam("limit", query.limit());
                                            }
                                            if (query.offset() != null) {
                                                uriBuilder.queryParam("offset", query.offset());
                                            }
                                            return uriBuilder.build();
                                        })
                                .retrieve()
                                .onStatus(HttpStatusCode::isError, AoiClient::fail)
                                .body(TaskList.class));
    }

    /** {@code GET /api/tasks/{id}}. */
    public TaskView getTask(UUID id) {
        return call(
                () ->
                        http.get()
                                .uri("/api/tasks/{id}", id)
                                .retrieve()
                                .onStatus(HttpStatusCode::isError, AoiClient::fail)
                                .body(TaskView.class));
    }

    private static <T> T call(java.util.function.Supplier<T> request) {
        try {
            return request.get();
        } catch (AoiApiException alreadyMapped) {
            throw alreadyMapped;
        } catch (RestClientException failure) {
            throw new AoiApiException("request to the AOI API failed: " + failure.getMessage(), failure);
        }
    }

    private static void fail(
            org.springframework.http.HttpRequest request,
            org.springframework.http.client.ClientHttpResponse response)
            throws java.io.IOException {
        int status = response.getStatusCode().value();
        String body = new String(response.getBody().readAllBytes(), java.nio.charset.StandardCharsets.UTF_8);
        throw new AoiApiException(status, "HTTP " + status + ": " + detailOf(body));
    }

    /** FastAPI errors are {@code {"detail": ...}}; 422 carries a list. Fall back to the raw body. */
    static String detailOf(String body) {
        try {
            var node = MAPPER.readTree(body);
            if (node != null && node.has("detail")) {
                var detail = node.get("detail");
                return detail.isTextual() ? detail.asText() : detail.toString();
            }
        } catch (java.io.IOException notJson) {
            // fall through to the raw body
        }
        return body.isBlank() ? "(empty response)" : body.strip();
    }

    private static boolean hasText(String value) {
        return value != null && !value.isBlank();
    }
}
