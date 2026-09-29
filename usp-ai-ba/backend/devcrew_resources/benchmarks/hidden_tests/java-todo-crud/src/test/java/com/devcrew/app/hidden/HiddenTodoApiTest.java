package com.devcrew.app.hidden;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.delete;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.patch;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.put;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.util.HashSet;
import java.util.Set;
import java.util.UUID;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.http.MediaType;
import org.springframework.mock.web.MockHttpServletResponse;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.RequestBuilder;
import org.springframework.test.web.servlet.request.MockHttpServletRequestBuilder;

@SpringBootTest
@AutoConfigureMockMvc
class HiddenTodoApiTest {

    @Autowired
    private MockMvc mvc;

    private final ObjectMapper json = new ObjectMapper();

    private MockHttpServletResponse call(RequestBuilder request) throws Exception {
        return mvc.perform(request).andReturn().getResponse();
    }

    private MockHttpServletResponse send(MockHttpServletRequestBuilder request, String body) throws Exception {
        return call(request.contentType(MediaType.APPLICATION_JSON).content(body));
    }

    private JsonNode body(MockHttpServletResponse response) throws Exception {
        return json.readTree(response.getContentAsString());
    }

    private JsonNode expect(int status, MockHttpServletResponse response) throws Exception {
        assertThat(response.getStatus()).as(response.getContentAsString()).isEqualTo(status);
        String text = response.getContentAsString();
        return text.isEmpty() ? null : json.readTree(text);
    }

    private static String uid() {
        return UUID.randomUUID().toString().substring(0, 8);
    }

    private static Set<Long> ids(JsonNode array) {
        Set<Long> out = new HashSet<>();
        array.forEach(node -> out.add(node.get("id").asLong()));
        return out;
    }

    private JsonNode create(String title) throws Exception {
        return expect(201, send(post("/api/todos"), "{\"title\":\"" + title + "\"}"));
    }

    @Test
    void createDefaultsDoneToFalse() throws Exception {
        JsonNode todo = create("buy milk");
        assertThat(todo.get("id").asLong()).isPositive();
        assertThat(todo.get("title").asText()).isEqualTo("buy milk");
        assertThat(todo.get("done").asBoolean()).isFalse();
    }

    @Test
    void createRejectsInvalidTitles() throws Exception {
        expect(400, send(post("/api/todos"), "{\"title\":\"\"}"));
        expect(400, send(post("/api/todos"), "{\"title\":\"   \"}"));
        expect(400, send(post("/api/todos"), "{\"title\":\"" + "x".repeat(201) + "\"}"));
        expect(400, send(post("/api/todos"), "{}"));
    }

    @Test
    void listContainsCreatedTodos() throws Exception {
        long a = create("list a").get("id").asLong();
        long b = expect(201, send(post("/api/todos"), "{\"title\":\"list b\",\"done\":true}")).get("id").asLong();
        assertThat(ids(expect(200, call(get("/api/todos"))))).contains(a, b);
    }

    @Test
    void getOneAnd404() throws Exception {
        JsonNode todo = create("get me");
        assertThat(expect(200, call(get("/api/todos/" + todo.get("id").asLong())))).isEqualTo(todo);
        expect(404, call(get("/api/todos/999999")));
    }

    @Test
    void putReplaces() throws Exception {
        long id = create("old").get("id").asLong();
        JsonNode updated = expect(200, send(put("/api/todos/" + id), "{\"title\":\"new\",\"done\":true}"));
        assertThat(updated.get("title").asText()).isEqualTo("new");
        assertThat(updated.get("done").asBoolean()).isTrue();
        assertThat(expect(200, call(get("/api/todos/" + id))).get("done").asBoolean()).isTrue();
    }

    @Test
    void putMissingAndInvalid() throws Exception {
        expect(404, send(put("/api/todos/999999"), "{\"title\":\"x\",\"done\":false}"));
        long id = create("valid").get("id").asLong();
        expect(400, send(put("/api/todos/" + id), "{\"title\":\"\",\"done\":false}"));
    }

    @Test
    void deleteThenGone() throws Exception {
        long id = create("delete me").get("id").asLong();
        MockHttpServletResponse response = call(delete("/api/todos/" + id));
        assertThat(response.getStatus()).isEqualTo(204);
        assertThat(response.getContentAsString()).isEmpty();
        expect(404, call(get("/api/todos/" + id)));
        expect(404, call(delete("/api/todos/" + id)));
    }

    @Test
    void idsAreNotReused() throws Exception {
        long first = create("first").get("id").asLong();
        call(delete("/api/todos/" + first));
        assertThat(create("second").get("id").asLong()).isNotEqualTo(first);
    }

    @Test
    void healthStillWorks() throws Exception {
        expect(200, call(get("/api/health")));
    }
}
