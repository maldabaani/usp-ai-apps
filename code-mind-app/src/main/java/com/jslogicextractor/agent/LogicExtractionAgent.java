package com.jslogicextractor.agent;

import com.jslogicextractor.scanner.SourceFile;

public interface LogicExtractionAgent {

    String name();

    ExtractionResult extract(SourceFile file);
}
