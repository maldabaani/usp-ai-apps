package com.jslogicextractor.monitoring;

import com.fasterxml.jackson.databind.ObjectMapper;

import java.io.File;
import java.io.IOException;
import java.time.Instant;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.List;
import java.util.concurrent.locks.ReentrantLock;

/**
 * In-memory ring buffer of captured error records, mirroring StoryForge's
 * monitoring/error_log.py. A plain static singleton rather than a Spring
 * bean, because ErrorCaptureAppender is instantiated by Logback's own
 * configuration loader before the Spring context exists, so it can't use
 * constructor injection -- it calls {@link #record} directly. Persisted to
 * disk so restarts don't lose history, path overridable via the
 * CODEMIND_ERROR_LOG_PATH env var (same override convention as
 * RuntimeSettings' codemind.runtime-settings-path).
 */
public final class ErrorLogStore {

    private static final int MAX_ENTRIES = 500;
    private static final ObjectMapper MAPPER = new ObjectMapper();
    private static final ReentrantLock LOCK = new ReentrantLock();
    private static final List<ErrorRecord> ENTRIES = new ArrayList<>();
    private static final File STORE_FILE = new File(
            System.getenv().getOrDefault("CODEMIND_ERROR_LOG_PATH", "./codemind-error-log.json"));

    static {
        load();
    }

    private ErrorLogStore() {
    }

    public record ErrorRecord(long timestamp, String logger, String level, String message, String traceback) {
    }

    public static void record(String logger, String level, String message, String traceback) {
        LOCK.lock();
        try {
            ENTRIES.add(new ErrorRecord(Instant.now().toEpochMilli(), logger, level, message, traceback));
            while (ENTRIES.size() > MAX_ENTRIES) {
                ENTRIES.remove(0);
            }
            persist();
        } finally {
            LOCK.unlock();
        }
    }

    public static List<ErrorRecord> list() {
        LOCK.lock();
        try {
            List<ErrorRecord> reversed = new ArrayList<>(ENTRIES);
            Collections.reverse(reversed);
            return reversed;
        } finally {
            LOCK.unlock();
        }
    }

    private static void persist() {
        try {
            MAPPER.writeValue(STORE_FILE, ENTRIES);
        } catch (IOException e) {
            // Best-effort persistence -- a write failure here must never
            // crash whatever triggered the error being logged in the first place.
        }
    }

    private static void load() {
        if (!STORE_FILE.isFile()) {
            return;
        }
        try {
            ErrorRecord[] loaded = MAPPER.readValue(STORE_FILE, ErrorRecord[].class);
            ENTRIES.addAll(Arrays.asList(loaded));
        } catch (IOException e) {
            // Corrupt/unreadable store file -- start fresh rather than fail startup.
        }
    }
}
