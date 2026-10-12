package dev.sujendra.aoi.client;

import java.time.Duration;
import org.springframework.boot.context.properties.ConfigurationProperties;

/**
 * Connection settings, bound from {@code aoi.client.*} when used with Spring Boot.
 *
 * <p>Credentials: the API accepts either {@code X-API-Key} (the deployment's ingest key, development
 * mode) or {@code Authorization: Bearer} (an OIDC access token or a workspace extension token, SaaS
 * mode). Set whichever applies; both may be set.
 */
@ConfigurationProperties(prefix = "aoi.client")
public class AoiClientProperties {

    /** Base URL of the API, for example {@code http://localhost:8000}. */
    private String baseUrl;

    /** Sent as {@code X-API-Key}. */
    private String apiKey;

    /** Sent as {@code Authorization: Bearer}. */
    private String bearerToken;

    /** Sent as {@code X-Workspace-Id} when the credential belongs to several workspaces. */
    private String workspaceId;

    private Duration connectTimeout = Duration.ofSeconds(5);

    /** Extraction runs inside the request by default, so this has to cover an LLM call. */
    private Duration readTimeout = Duration.ofSeconds(120);

    public String getBaseUrl() {
        return baseUrl;
    }

    public void setBaseUrl(String baseUrl) {
        this.baseUrl = baseUrl;
    }

    public String getApiKey() {
        return apiKey;
    }

    public void setApiKey(String apiKey) {
        this.apiKey = apiKey;
    }

    public String getBearerToken() {
        return bearerToken;
    }

    public void setBearerToken(String bearerToken) {
        this.bearerToken = bearerToken;
    }

    public String getWorkspaceId() {
        return workspaceId;
    }

    public void setWorkspaceId(String workspaceId) {
        this.workspaceId = workspaceId;
    }

    public Duration getConnectTimeout() {
        return connectTimeout;
    }

    public void setConnectTimeout(Duration connectTimeout) {
        this.connectTimeout = connectTimeout;
    }

    public Duration getReadTimeout() {
        return readTimeout;
    }

    public void setReadTimeout(Duration readTimeout) {
        this.readTimeout = readTimeout;
    }
}
