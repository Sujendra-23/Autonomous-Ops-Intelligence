package dev.sujendra.aoi.client;

import static org.assertj.core.api.Assertions.assertThat;

import dev.sujendra.aoi.client.Models.SubmitTranscript;
import dev.sujendra.aoi.client.Models.TaskQuery;
import dev.sujendra.aoi.client.Models.TranscriptDetail;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;

/**
 * Talks to a real API when {@code AOI_BASE_URL} is set (optionally {@code AOI_API_KEY}); skipped in
 * normal builds. It queues a transcript, so no LLM key is needed on the server.
 */
@EnabledIfEnvironmentVariable(named = "AOI_BASE_URL", matches = ".+")
class LiveApiIT {

    @Test
    void queuedTranscriptRoundTripsAgainstTheRealApi() {
        AoiClientProperties properties = new AoiClientProperties();
        properties.setBaseUrl(System.getenv("AOI_BASE_URL"));
        properties.setApiKey(System.getenv("AOI_API_KEY"));
        AoiClient client = new AoiClient(properties);

        TranscriptDetail created =
                client.submitTranscript(
                        SubmitTranscript.of("Java client live check", "Priya will rotate the staging keys by Friday.")
                                .queued());
        TranscriptDetail fetched = client.getTranscript(created.id());

        assertThat(fetched.id()).isEqualTo(created.id());
        assertThat(fetched.title()).isEqualTo("Java client live check");
        assertThat(client.listTasks(TaskQuery.all().withPage(5, 0)).items()).isNotNull();
    }
}
