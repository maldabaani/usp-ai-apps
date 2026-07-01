package com.jslogicextractor.agent;

import com.jslogicextractor.scanner.SourceFile;
import org.junit.jupiter.api.Test;

import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class AgentSelectorTest {

    @Test
    void roundRobinsAcrossMultipleAgents() {
        LogicExtractionAgent a = stub("a");
        LogicExtractionAgent b = stub("b");
        AgentSelector selector = new AgentSelector(List.of(a, b));

        assertThat(selector.next().name()).isEqualTo("a");
        assertThat(selector.next().name()).isEqualTo("b");
        assertThat(selector.next().name()).isEqualTo("a");
    }

    @Test
    void singleAgentAlwaysReturnsItself() {
        LogicExtractionAgent only = stub("only");
        AgentSelector selector = new AgentSelector(List.of(only));

        assertThat(selector.next()).isSameAs(only);
        assertThat(selector.next()).isSameAs(only);
    }

    @Test
    void rejectsEmptyAgentList() {
        assertThatThrownBy(() -> new AgentSelector(List.of()))
                .isInstanceOf(IllegalStateException.class);
    }

    private LogicExtractionAgent stub(String agentName) {
        return new LogicExtractionAgent() {
            @Override
            public String name() {
                return agentName;
            }

            @Override
            public ExtractionResult extract(SourceFile file) {
                throw new UnsupportedOperationException("not used in this test");
            }
        };
    }
}
