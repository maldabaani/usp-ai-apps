package com.jslogicextractor.monitoring;

import ch.qos.logback.classic.Level;
import ch.qos.logback.classic.spi.ILoggingEvent;
import org.junit.jupiter.api.Test;

import java.util.UUID;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

class ErrorCaptureAppenderTest {

    @Test
    void appendCapturesLoggerLevelMessageWithNoThrowable() {
        ErrorCaptureAppender appender = new ErrorCaptureAppender();
        appender.start();

        String uniqueMessage = "appender-test-" + UUID.randomUUID();
        ILoggingEvent event = mock(ILoggingEvent.class);
        when(event.getLoggerName()).thenReturn("com.jslogicextractor.test.SomeClass");
        when(event.getLevel()).thenReturn(Level.ERROR);
        when(event.getFormattedMessage()).thenReturn(uniqueMessage);
        when(event.getThrowableProxy()).thenReturn(null);

        appender.doAppend(event);

        ErrorLogStore.ErrorRecord latest = ErrorLogStore.list().get(0);
        assertEquals("com.jslogicextractor.test.SomeClass", latest.logger());
        assertEquals("ERROR", latest.level());
        assertEquals(uniqueMessage, latest.message());
        assertNull(latest.traceback());
    }
}
