package com.jslogicextractor.monitoring;

import ch.qos.logback.classic.spi.ILoggingEvent;
import ch.qos.logback.classic.spi.IThrowableProxy;
import ch.qos.logback.classic.spi.ThrowableProxyUtil;
import ch.qos.logback.core.AppenderBase;

/**
 * Registered in logback-spring.xml at ERROR level for the root logger, so
 * every {@code log.error(...)}/uncaught exception logged anywhere in the
 * codebase is captured into {@link ErrorLogStore} with no per-call-site
 * changes needed -- the Java analogue of StoryForge's
 * monitoring/log_capture.py logging.Handler.
 */
public class ErrorCaptureAppender extends AppenderBase<ILoggingEvent> {

    @Override
    protected void append(ILoggingEvent event) {
        IThrowableProxy throwableProxy = event.getThrowableProxy();
        String traceback = throwableProxy != null ? ThrowableProxyUtil.asString(throwableProxy) : null;
        ErrorLogStore.record(event.getLoggerName(), event.getLevel().toString(), event.getFormattedMessage(), traceback);
    }
}
