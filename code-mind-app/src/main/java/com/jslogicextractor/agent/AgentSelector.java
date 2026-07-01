package com.jslogicextractor.agent;

import org.springframework.stereotype.Component;

import java.util.List;
import java.util.concurrent.atomic.AtomicInteger;

// Round-robins across every LogicExtractionAgent bean; adding agent beans (e.g. extra API keys/models) scales throughput with no orchestrator changes.
@Component
public class AgentSelector {

    private final List<LogicExtractionAgent> agents;
    private final AtomicInteger counter = new AtomicInteger();

    public AgentSelector(List<LogicExtractionAgent> agents) {
        if (agents == null || agents.isEmpty()) {
            throw new IllegalStateException("No LogicExtractionAgent beans configured");
        }
        this.agents = List.copyOf(agents);
    }

    public LogicExtractionAgent next() {
        int index = Math.floorMod(counter.getAndIncrement(), agents.size());
        return agents.get(index);
    }

    public int agentCount() {
        return agents.size();
    }
}
