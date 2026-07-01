package com.jslogicextractor.output;

import com.jslogicextractor.agent.ExtractionResult;
import com.jslogicextractor.orchestration.ExtractionJob;

public interface ExtractionResultWriter {

    boolean exists(ExtractionJob job, String relativePath);

    void write(ExtractionJob job, ExtractionResult result);

    void writeSummary(ExtractionJob job);
}
