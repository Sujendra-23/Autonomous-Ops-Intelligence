package dev.sujendra.aoi.client;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.annotation.JsonInclude;
import com.fasterxml.jackson.annotation.JsonProperty;
import com.fasterxml.jackson.databind.annotation.JsonDeserialize;
import java.time.Instant;
import java.util.List;
import java.util.UUID;

/** Request and response types, mirroring the API's JSON (snake_case on the wire). */
public final class Models {

    private Models() {}

    /** Body of {@code POST /api/transcripts}. Null fields are omitted. */
    @JsonInclude(JsonInclude.Include.NON_NULL)
    public record SubmitTranscript(
            String title,
            String content,
            String source,
            @JsonProperty("meeting_date") Instant meetingDate,
            List<String> participants,
            @JsonProperty("project_hint") String projectHint,
            @JsonProperty("sync_extract") Boolean syncExtract) {

        /** A transcript extracted synchronously, which is what the API does by default. */
        public static SubmitTranscript of(String title, String content) {
            return new SubmitTranscript(title, content, null, null, null, null, null);
        }

        /** Same, but queued: the call returns at once and the worker extracts later. */
        public SubmitTranscript queued() {
            return new SubmitTranscript(
                    title, content, source, meetingDate, participants, projectHint, false);
        }
    }

    @JsonIgnoreProperties(ignoreUnknown = true)
    public record TaskView(
            UUID id,
            String title,
            String description,
            String owner,
            @JsonProperty("due_date") @JsonDeserialize(using = LenientInstantDeserializer.class)
                    Instant dueDate,
            String status,
            String priority,
            @JsonProperty("source_quote") String sourceQuote,
            Double confidence,
            @JsonProperty("project_id") UUID projectId,
            @JsonProperty("transcript_id") UUID transcriptId,
            @JsonProperty("linear_issue_url") String linearIssueUrl,
            @JsonProperty("jira_issue_url") String jiraIssueUrl,
            @JsonProperty("salesforce_task_url") String salesforceTaskUrl) {}

    @JsonIgnoreProperties(ignoreUnknown = true)
    public record Decision(
            UUID id,
            String summary,
            String rationale,
            @JsonProperty("decided_by") List<String> decidedBy,
            @JsonProperty("source_quote") String sourceQuote) {}

    @JsonIgnoreProperties(ignoreUnknown = true)
    public record Risk(
            UUID id,
            String title,
            String severity,
            String likelihood,
            String mitigation,
            @JsonProperty("source_quote") String sourceQuote) {}

    @JsonIgnoreProperties(ignoreUnknown = true)
    public record Blocker(
            UUID id,
            String summary,
            @JsonProperty("blocked_party") String blockedParty,
            @JsonProperty("needs_from") String needsFrom,
            String severity,
            String status) {}

    /** The same shape for every ingest path: transcript, uploaded file, or document. */
    @JsonIgnoreProperties(ignoreUnknown = true)
    public record TranscriptDetail(
            UUID id,
            String title,
            String status,
            String source,
            String error,
            @JsonProperty("project_id") UUID projectId,
            @JsonDeserialize(using = LenientInstantDeserializer.class)
                    @JsonProperty("processed_at")
                    Instant processedAt,
            List<TaskView> tasks,
            List<Decision> decisions,
            List<Risk> risks,
            List<Blocker> blockers) {

        public TranscriptDetail {
            tasks = tasks == null ? List.of() : tasks;
            decisions = decisions == null ? List.of() : decisions;
            risks = risks == null ? List.of() : risks;
            blockers = blockers == null ? List.of() : blockers;
        }

        /** Extraction has reached a final state: {@code completed} or {@code failed}. */
        public boolean isFinished() {
            return "completed".equals(status) || "failed".equals(status);
        }
    }

    @JsonIgnoreProperties(ignoreUnknown = true)
    public record TaskList(List<TaskView> items, int total) {}

    /** Filters for {@code GET /api/tasks}; unset fields are not sent. */
    public record TaskQuery(
            String status, UUID projectId, String owner, boolean overdue, Integer limit, Integer offset) {

        public static TaskQuery all() {
            return new TaskQuery(null, null, null, false, null, null);
        }

        public TaskQuery withStatus(String value) {
            return new TaskQuery(value, projectId, owner, overdue, limit, offset);
        }

        public TaskQuery withOwner(String value) {
            return new TaskQuery(status, projectId, value, overdue, limit, offset);
        }

        public TaskQuery onlyOverdue() {
            return new TaskQuery(status, projectId, owner, true, limit, offset);
        }

        public TaskQuery withPage(int pageLimit, int pageOffset) {
            return new TaskQuery(status, projectId, owner, overdue, pageLimit, pageOffset);
        }
    }
}
