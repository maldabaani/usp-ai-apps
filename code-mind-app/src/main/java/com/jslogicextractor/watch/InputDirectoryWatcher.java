package com.jslogicextractor.watch;

import com.jslogicextractor.config.WatchProperties;
import com.jslogicextractor.orchestration.JobStarter;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.context.SmartLifecycle;
import org.springframework.stereotype.Component;

import java.io.IOException;
import java.nio.file.ClosedWatchServiceException;
import java.nio.file.FileSystems;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.WatchEvent;
import java.nio.file.WatchKey;
import java.nio.file.WatchService;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.ScheduledFuture;
import java.util.concurrent.TimeUnit;

import static java.nio.file.StandardWatchEventKinds.ENTRY_CREATE;
import static java.nio.file.StandardWatchEventKinds.ENTRY_MODIFY;
import static java.nio.file.StandardWatchEventKinds.OVERFLOW;

/**
 * Watches {@code jsprocessor.watch.directory} (non-recursively) and auto-starts one extraction job
 * per file that appears in it — a dropped subfolder is ignored, since the watch unit is an
 * individual file, not a directory. Off by default; enabling it activates no other behavior.
 *
 * <p>Each create/modify event for a path (re)schedules a debounced check after
 * {@code quietPeriodMillis} of inactivity on that path, so a file still being written/copied into
 * the directory isn't picked up mid-write. Files present before the watcher starts are not
 * retroactively picked up — only files that arrive while it's running.
 */
@Component
@ConditionalOnProperty(prefix = "jsprocessor.watch", name = "enabled", havingValue = "true")
public class InputDirectoryWatcher implements SmartLifecycle {

    private static final Logger log = LoggerFactory.getLogger(InputDirectoryWatcher.class);

    private final JobStarter jobStarter;
    private final WatchProperties properties;
    private final Map<Path, ScheduledFuture<?>> pendingChecks = new ConcurrentHashMap<>();

    private volatile ScheduledExecutorService debounceExecutor;
    private volatile WatchService watchService;
    private volatile Thread watchThread;
    private volatile boolean running;

    public InputDirectoryWatcher(JobStarter jobStarter, WatchProperties properties) {
        this.jobStarter = jobStarter;
        this.properties = properties;
    }

    @Override
    public void start() {
        Path directory = Path.of(properties.directory()).toAbsolutePath().normalize();
        try {
            Files.createDirectories(directory);
            watchService = FileSystems.getDefault().newWatchService();
            directory.register(watchService, ENTRY_CREATE, ENTRY_MODIFY);
        } catch (IOException e) {
            log.error("Failed to start input directory watcher on {}: {}", directory, e.getMessage());
            return;
        }
        debounceExecutor = Executors.newSingleThreadScheduledExecutor();
        running = true;
        watchThread = new Thread(() -> watchLoop(directory), "input-directory-watcher");
        watchThread.setDaemon(true);
        watchThread.start();
        log.info("Watching {} for dropped files; each becomes its own extraction job", directory);
    }

    private void watchLoop(Path directory) {
        while (running) {
            WatchKey key;
            try {
                key = watchService.poll(1, TimeUnit.SECONDS);
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
                return;
            } catch (ClosedWatchServiceException e) {
                return;
            }
            if (key == null) {
                continue;
            }
            for (WatchEvent<?> event : key.pollEvents()) {
                if (event.kind() == OVERFLOW) {
                    continue;
                }
                Path child = directory.resolve((Path) event.context());
                scheduleCheck(child);
            }
            if (!key.reset()) {
                return;
            }
        }
    }

    private void scheduleCheck(Path file) {
        pendingChecks.compute(file, (path, existing) -> {
            if (existing != null) {
                existing.cancel(false);
            }
            return debounceExecutor.schedule(() -> startJobIfStillPresent(path),
                    properties.quietPeriodMillis(), TimeUnit.MILLISECONDS);
        });
    }

    private void startJobIfStillPresent(Path file) {
        pendingChecks.remove(file);
        if (!Files.isRegularFile(file)) {
            // Directory dropped directly into the watched folder, or the file was already
            // moved/deleted before the quiet period elapsed — neither starts a job.
            return;
        }
        try {
            var job = jobStarter.startForFile(file);
            log.info("Auto-started job {} for dropped file {}", job.id(), file);
        } catch (Exception e) {
            log.warn("Failed to auto-start job for {}: {}", file, e.getMessage());
        }
    }

    @Override
    public void stop() {
        running = false;
        pendingChecks.values().forEach(future -> future.cancel(false));
        pendingChecks.clear();
        if (debounceExecutor != null) {
            debounceExecutor.shutdownNow();
        }
        if (watchService != null) {
            try {
                watchService.close();
            } catch (IOException ignored) {
                // Best-effort close on shutdown.
            }
        }
        if (watchThread != null) {
            watchThread.interrupt();
        }
    }

    @Override
    public boolean isRunning() {
        return running;
    }
}
