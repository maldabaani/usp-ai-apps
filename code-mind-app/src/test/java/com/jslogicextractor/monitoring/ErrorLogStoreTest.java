package com.jslogicextractor.monitoring;

import org.junit.jupiter.api.Test;

import java.util.List;
import java.util.UUID;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * ErrorLogStore is a static singleton (see its own docstring for why -- its
 * appender is instantiated by Logback before the Spring context exists), so
 * these tests assert relative behavior (most-recent-first ordering, the ring
 * buffer's cap) rather than exact counts, since other tests running earlier
 * in the same JVM may have already logged unrelated ERROR-level records into
 * the same in-memory buffer.
 */
class ErrorLogStoreTest {

    @Test
    void recordsAppearMostRecentFirst() {
        String unique = UUID.randomUUID().toString();
        ErrorLogStore.record("test.logger.A", "ERROR", "first-" + unique, "trace-first-" + unique);
        ErrorLogStore.record("test.logger.A", "ERROR", "second-" + unique, "trace-second-" + unique);

        ErrorLogStore.ErrorRecord latest = ErrorLogStore.list().get(0);
        assertEquals("second-" + unique, latest.message());
        assertEquals("trace-second-" + unique, latest.traceback());
        assertEquals("test.logger.A", latest.logger());
        assertEquals("ERROR", latest.level());
    }

    @Test
    void ringBufferNeverExceedsCapAndKeepsMostRecent() {
        String unique = UUID.randomUUID().toString();
        for (int i = 0; i < 600; i++) {
            ErrorLogStore.record("test.logger.cap", "ERROR", "cap-" + unique + "-" + i, null);
        }

        List<ErrorLogStore.ErrorRecord> all = ErrorLogStore.list();
        assertTrue(all.size() <= 500, "ring buffer must never exceed 500 entries, was " + all.size());
        assertEquals("cap-" + unique + "-599", all.get(0).message());
    }
}
