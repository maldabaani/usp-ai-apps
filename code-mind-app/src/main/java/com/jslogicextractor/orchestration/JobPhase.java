package com.jslogicextractor.orchestration;

public enum JobPhase {
    PENDING,
    SCANNING,
    FILTERING,
    PROCESSING,
    COMPLETED,
    CANCELLED,
    FAILED
}
