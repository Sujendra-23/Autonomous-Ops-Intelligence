package dev.sujendra.aoi.client;

/** The API answered with a non-2xx status, or could not be reached. */
public class AoiApiException extends RuntimeException {

    private final int status;

    /** HTTP status, or {@code -1} when no response was received. */
    public int getStatus() {
        return status;
    }

    public AoiApiException(int status, String message) {
        super(message);
        this.status = status;
    }

    public AoiApiException(String message, Throwable cause) {
        super(message, cause);
        this.status = -1;
    }
}
