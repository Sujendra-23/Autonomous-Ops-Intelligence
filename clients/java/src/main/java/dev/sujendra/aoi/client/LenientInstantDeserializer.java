package dev.sujendra.aoi.client;

import com.fasterxml.jackson.core.JsonParser;
import com.fasterxml.jackson.databind.DeserializationContext;
import com.fasterxml.jackson.databind.JsonDeserializer;
import java.io.IOException;
import java.time.Instant;
import java.time.LocalDateTime;
import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.time.format.DateTimeParseException;

/**
 * Parses the API's timestamps. Some fields are timezone-aware ({@code 2026-03-14T00:00:00Z}) and
 * some are naive UTC ({@code 2026-03-14T09:30:00.123456}, written by {@code datetime.utcnow()}); a
 * naive value is read as UTC.
 */
final class LenientInstantDeserializer extends JsonDeserializer<Instant> {

    @Override
    public Instant deserialize(JsonParser parser, DeserializationContext context) throws IOException {
        String text = parser.getValueAsString();
        if (text == null || text.isBlank()) {
            return null;
        }
        try {
            return OffsetDateTime.parse(text).toInstant();
        } catch (DateTimeParseException withOffsetFailed) {
            try {
                return LocalDateTime.parse(text).toInstant(ZoneOffset.UTC);
            } catch (DateTimeParseException naiveFailed) {
                return (Instant) context.handleWeirdStringValue(
                        Instant.class, text, "not an ISO-8601 timestamp");
            }
        }
    }
}
