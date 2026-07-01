package com.jslogicextractor.agent;

import com.jslogicextractor.prompt.LogicExtractionPromptTemplates;
import com.jslogicextractor.scanner.SourceFile;
import org.junit.jupiter.api.Test;
import org.springframework.ai.chat.client.ChatClient;
import org.springframework.ai.chat.messages.AssistantMessage;
import org.springframework.ai.chat.metadata.ChatResponseMetadata;
import org.springframework.ai.chat.metadata.DefaultUsage;
import org.springframework.ai.chat.model.ChatResponse;
import org.springframework.ai.chat.model.Generation;
import org.springframework.ai.chat.prompt.Prompt;

import java.nio.file.Path;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

class OllamaLogicExtractionAgentTest {

    private final LogicExtractionPromptTemplates promptTemplates = mock(LogicExtractionPromptTemplates.class);

    @Test
    void returnsSuccessfulExtractionWithUsageOnHappyPath() {
        ChatClient chatClient = mock(ChatClient.class);
        ChatClient.ChatClientRequestSpec requestSpec = mock(ChatClient.ChatClientRequestSpec.class);
        ChatClient.CallResponseSpec callResponseSpec = mock(ChatClient.CallResponseSpec.class);

        SourceFile file = sourceFile();
        Prompt prompt = new Prompt("extract the logic");
        when(promptTemplates.buildExtractionPrompt(file)).thenReturn(prompt);
        when(chatClient.prompt(prompt)).thenReturn(requestSpec);
        when(requestSpec.call()).thenReturn(callResponseSpec);
        when(callResponseSpec.chatResponse()).thenReturn(chatResponseWithUsage("extracted logic", 100, 50));

        OllamaLogicExtractionAgent agent = new OllamaLogicExtractionAgent(chatClient, promptTemplates);
        ExtractionResult result = agent.extract(file);

        assertThat(result.success()).isTrue();
        assertThat(result.agentName()).isEqualTo("ollama-logic-extractor");
        assertThat(result.content()).isEqualTo("extracted logic");
        assertThat(result.promptTokens()).isEqualTo(100);
        assertThat(result.completionTokens()).isEqualTo(50);
    }

    @Test
    void returnsFailureWhenChatClientThrows() {
        ChatClient chatClient = mock(ChatClient.class);
        SourceFile file = sourceFile();
        when(promptTemplates.buildExtractionPrompt(file)).thenReturn(new Prompt("extract the logic"));
        when(chatClient.prompt(any(Prompt.class))).thenThrow(new RuntimeException("connection refused"));

        OllamaLogicExtractionAgent agent = new OllamaLogicExtractionAgent(chatClient, promptTemplates);
        ExtractionResult result = agent.extract(file);

        assertThat(result.success()).isFalse();
        assertThat(result.errorMessage()).isEqualTo("connection refused");
    }

    private ChatResponse chatResponseWithUsage(String text, int promptTokens, int completionTokens) {
        Generation generation = new Generation(new AssistantMessage(text));
        ChatResponseMetadata metadata = ChatResponseMetadata.builder()
                .usage(new DefaultUsage(promptTokens, completionTokens))
                .build();
        return new ChatResponse(List.of(generation), metadata);
    }

    private SourceFile sourceFile() {
        return new SourceFile(Path.of("/repo/src/index.js"), "src/index.js", "console.log('hi');", 19);
    }
}
