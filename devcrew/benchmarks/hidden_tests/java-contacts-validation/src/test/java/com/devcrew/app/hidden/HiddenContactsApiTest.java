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
class HiddenContactsApiTest {

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

    private static String contact(String name, String email, String phone) {
        String phonePart = phone == null ? "" : ",\"phone\":\"" + phone + "\"";
        return "{\"name\":\"" + name + "\",\"email\":\"" + email + "\"" + phonePart + "}";
    }

    private JsonNode create(String name, String email) throws Exception {
        return expect(201, send(post("/api/contacts"), contact(name, email, null)));
    }

    @Test
    void createWithAndWithoutPhone() throws Exception {
        String email = uid() + "@example.com";
        JsonNode plain = create("Ada", email);
        assertThat(plain.get("id").asLong()).isPositive();
        assertThat(plain.get("email").asText()).isEqualTo(email);
        assertThat(plain.get("phone") == null || plain.get("phone").isNull()).isTrue();
        JsonNode withPhone = expect(201, send(post("/api/contacts"), contact("Bob", uid() + "@example.com", "+49 170-1234567")));
        assertThat(withPhone.get("phone").asText()).isEqualTo("+49 170-1234567");
    }

    @Test
    void validationErrorsAreReportedPerField() throws Exception {
        JsonNode errors = expect(400, send(post("/api/contacts"), contact(" ", "not-an-email", "abc"))).get("errors");
        assertThat(errors).isNotNull();
        assertThat(errors.has("name")).isTrue();
        assertThat(errors.has("email")).isTrue();
        assertThat(errors.has("phone")).isTrue();
    }

    @Test
    void singleInvalidFieldOnly() throws Exception {
        JsonNode errors = expect(400, send(post("/api/contacts"), contact("Valid", "bad", null))).get("errors");
        assertThat(errors.has("email")).isTrue();
        assertThat(errors.has("name")).isFalse();
    }

    @Test
    void duplicateEmailIsCaseInsensitive() throws Exception {
        String email = uid() + "@example.com";
        create("First", email);
        expect(409, send(post("/api/contacts"), contact("Second", email.toUpperCase(), null)));
    }

    @Test
    void listIsSortedByNameAndSearchable() throws Exception {
        String tag = uid();
        long zed = create("zed " + tag, uid() + "@example.com").get("id").asLong();
        long amy = create("Amy " + tag, uid() + "@example.com").get("id").asLong();
        long mail = create("Other", tag + "@mail.test").get("id").asLong();
        JsonNode found = expect(200, call(get("/api/contacts").param("q", tag.toUpperCase())));
        assertThat(ids(found)).containsExactlyInAnyOrder(zed, amy, mail);
        JsonNode all = expect(200, call(get("/api/contacts")));
        String previous = "";
        for (JsonNode node : all) {
            String name = node.get("name").asText().toLowerCase();
            assertThat(name.compareTo(previous)).isGreaterThanOrEqualTo(0);
            previous = name;
        }
    }

    @Test
    void getPutDelete() throws Exception {
        long id = create("Carl", uid() + "@example.com").get("id").asLong();
        expect(200, call(get("/api/contacts/" + id)));
        String email = uid() + "@example.com";
        JsonNode updated = expect(200, send(put("/api/contacts/" + id), contact("Carl Jr", email, null)));
        assertThat(updated.get("name").asText()).isEqualTo("Carl Jr");
        expect(400, send(put("/api/contacts/" + id), contact("Carl", "bad", null)));
        expect(204, call(delete("/api/contacts/" + id)));
        expect(404, call(get("/api/contacts/" + id)));
        expect(404, call(delete("/api/contacts/" + id)));
        expect(404, send(put("/api/contacts/" + id), contact("X", uid() + "@example.com", null)));
    }

    @Test
    void updateKeepingOwnEmailIsAllowedButStealingIsNot() throws Exception {
        String taken = uid() + "@example.com";
        create("Owner", taken);
        String own = uid() + "@example.com";
        long id = create("Me", own).get("id").asLong();
        expect(200, send(put("/api/contacts/" + id), contact("Me again", own, null)));
        expect(409, send(put("/api/contacts/" + id), contact("Me", taken, null)));
    }
}
