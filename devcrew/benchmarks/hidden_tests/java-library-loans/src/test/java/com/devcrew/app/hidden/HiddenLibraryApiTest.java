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
class HiddenLibraryApiTest {

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

    private long author(String name) throws Exception {
        return expect(201, send(post("/api/authors"), "{\"name\":\"" + name + "\"}")).get("id").asLong();
    }

    private JsonNode book(long authorId, int copies) throws Exception {
        String body = "{\"title\":\"Earthsea\",\"authorId\":" + authorId + ",\"copies\":" + copies + "}";
        return expect(201, send(post("/api/books"), body));
    }

    private MockHttpServletResponse loan(long bookId, String borrower) throws Exception {
        return send(post("/api/loans"), "{\"bookId\":" + bookId + ",\"borrower\":\"" + borrower + "\"}");
    }

    @Test
    void authors() throws Exception {
        long id = author("Le Guin");
        assertThat(expect(200, call(get("/api/authors/" + id))).get("name").asText()).isEqualTo("Le Guin");
        expect(404, call(get("/api/authors/999999")));
        expect(400, send(post("/api/authors"), "{\"name\":\"\"}"));
    }

    @Test
    void bookStartsFullyAvailable() throws Exception {
        JsonNode created = book(author("A"), 3);
        assertThat(created.get("copies").asInt()).isEqualTo(3);
        assertThat(created.get("available").asInt()).isEqualTo(3);
        assertThat(expect(200, call(get("/api/books/" + created.get("id").asLong()))).get("available").asInt()).isEqualTo(3);
    }

    @Test
    void bookValidation() throws Exception {
        long authorId = author("B");
        expect(404, send(post("/api/books"), "{\"title\":\"t\",\"authorId\":999999,\"copies\":1}"));
        expect(400, send(post("/api/books"), "{\"title\":\"t\",\"authorId\":" + authorId + ",\"copies\":0}"));
        expect(404, call(get("/api/books/999999")));
    }

    @Test
    void authorBooks() throws Exception {
        long authorId = author("C");
        long b1 = book(authorId, 1).get("id").asLong();
        long b2 = book(authorId, 2).get("id").asLong();
        assertThat(ids(expect(200, call(get("/api/authors/" + authorId + "/books"))))).containsExactlyInAnyOrder(b1, b2);
        expect(404, call(get("/api/authors/999999/books")));
    }

    @Test
    void loanReducesAvailabilityAndConflictsWhenNoneLeft() throws Exception {
        long bookId = book(author("D"), 1).get("id").asLong();
        JsonNode created = expect(201, loan(bookId, "sam"));
        assertThat(created.get("returned").asBoolean()).isFalse();
        assertThat(created.get("bookId").asLong()).isEqualTo(bookId);
        assertThat(expect(200, call(get("/api/books/" + bookId))).get("available").asInt()).isZero();
        expect(409, loan(bookId, "kim"));
        expect(404, loan(999999, "x"));
    }

    @Test
    void returnRestoresCopy() throws Exception {
        long bookId = book(author("E"), 1).get("id").asLong();
        long loanId = expect(201, loan(bookId, "sam")).get("id").asLong();
        assertThat(expect(200, call(post("/api/loans/" + loanId + "/return"))).get("returned").asBoolean()).isTrue();
        assertThat(expect(200, call(get("/api/books/" + bookId))).get("available").asInt()).isEqualTo(1);
        expect(409, call(post("/api/loans/" + loanId + "/return")));
        expect(404, call(post("/api/loans/999999/return")));
    }

    @Test
    void activeLoansFilter() throws Exception {
        long bookId = book(author("F"), 2).get("id").asLong();
        long active = expect(201, loan(bookId, "a")).get("id").asLong();
        long done = expect(201, loan(bookId, "b")).get("id").asLong();
        call(post("/api/loans/" + done + "/return"));
        Set<Long> activeIds = ids(expect(200, call(get("/api/loans").param("active", "true"))));
        assertThat(activeIds).contains(active).doesNotContain(done);
        assertThat(ids(expect(200, call(get("/api/loans"))))).contains(active, done);
    }
}
