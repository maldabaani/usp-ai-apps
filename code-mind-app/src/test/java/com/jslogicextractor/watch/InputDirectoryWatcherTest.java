package com.jslogicextractor.watch;

import com.jslogicextractor.config.WatchProperties;
import com.jslogicextractor.orchestration.ExtractionJob;
import com.jslogicextractor.orchestration.JobStarter;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;
import java.util.UUID;

import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.after;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.timeout;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoMoreInteractions;
import static org.mockito.Mockito.when;

class InputDirectoryWatcherTest {

    @TempDir
    Path watchDirectory;

    private final JobStarter jobStarter = mock(JobStarter.class);
    private InputDirectoryWatcher watcher;

    @AfterEach
    void tearDown() {
        if (watcher != null) {
            watcher.stop();
        }
    }

    @Test
    void startsAJobForEachFileDroppedIntoTheWatchedDirectory() throws IOException {
        watcher = newWatcher(150);
        ExtractionJob job = new ExtractionJob(UUID.randomUUID(), watchDirectory.resolve("dropped.js"),
                watchDirectory.resolve("out"), 4);
        when(jobStarter.startForFile(eq(watchDirectory.resolve("dropped.js")))).thenReturn(job);

        watcher.start();
        Files.writeString(watchDirectory.resolve("dropped.js"), "const a = 1;");

        verify(jobStarter, timeout(3000)).startForFile(eq(watchDirectory.resolve("dropped.js")));
    }

    @Test
    void ignoresASubdirectoryDroppedIntoTheWatchedDirectory() throws IOException {
        watcher = newWatcher(150);

        watcher.start();
        Files.createDirectories(watchDirectory.resolve("dropped-folder"));

        // Wait out the full quiet period before asserting it never reacted.
        verify(jobStarter, after(1500).never()).startForFile(eq(watchDirectory.resolve("dropped-folder")));
        verifyNoMoreInteractions(jobStarter);
    }

    @Test
    void coalescesRapidWritesToTheSameFileIntoASingleJob() throws IOException, InterruptedException {
        watcher = newWatcher(200);
        Path file = watchDirectory.resolve("slow-copy.js");
        ExtractionJob job = new ExtractionJob(UUID.randomUUID(), file, watchDirectory.resolve("out"), 4);
        when(jobStarter.startForFile(eq(file))).thenReturn(job);

        watcher.start();
        Files.writeString(file, "const a = 1;");
        Thread.sleep(50);
        Files.write(file, "\nconst b = 2;".getBytes(), StandardOpenOption.APPEND);

        verify(jobStarter, timeout(3000)).startForFile(eq(file));
        Thread.sleep(500);
        verify(jobStarter, times(1)).startForFile(eq(file));
    }

    private InputDirectoryWatcher newWatcher(long quietPeriodMillis) {
        WatchProperties properties = new WatchProperties(true, watchDirectory.toString(), quietPeriodMillis);
        return new InputDirectoryWatcher(jobStarter, properties);
    }
}
