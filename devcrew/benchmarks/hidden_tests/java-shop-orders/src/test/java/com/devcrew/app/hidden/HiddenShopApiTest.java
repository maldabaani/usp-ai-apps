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
class HiddenShopApiTest {

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

    private String product(String price, int stock) throws Exception {
        String sku = "SKU-" + uid();
        String body = "{\"sku\":\"" + sku + "\",\"name\":\"Widget\",\"price\":" + price + ",\"stock\":" + stock + "}";
        expect(201, send(post("/api/products"), body));
        return sku;
    }

    private int stock(String sku) throws Exception {
        return expect(200, call(get("/api/products/" + sku))).get("stock").asInt();
    }

    private MockHttpServletResponse order(String... skuQuantityPairs) throws Exception {
        StringBuilder items = new StringBuilder();
        for (int i = 0; i < skuQuantityPairs.length; i += 2) {
            if (i > 0) {
                items.append(',');
            }
            items.append("{\"sku\":\"").append(skuQuantityPairs[i]).append("\",\"quantity\":").append(skuQuantityPairs[i + 1]).append('}');
        }
        return send(post("/api/orders"), "{\"items\":[" + items + "]}");
    }

    @Test
    void createAndGetProduct() throws Exception {
        String sku = product("3.25", 4);
        JsonNode p = expect(200, call(get("/api/products/" + sku)));
        assertThat(p.get("price").asDouble()).isEqualTo(3.25);
        assertThat(p.get("stock").asInt()).isEqualTo(4);
        expect(404, call(get("/api/products/NOPE-404")));
    }

    @Test
    void duplicateSkuAndValidation() throws Exception {
        String sku = product("1", 1);
        expect(409, send(post("/api/products"), "{\"sku\":\"" + sku + "\",\"name\":\"x\",\"price\":1,\"stock\":1}"));
        expect(400, send(post("/api/products"), "{\"sku\":\"N\",\"name\":\"x\",\"price\":0,\"stock\":1}"));
        expect(400, send(post("/api/products"), "{\"sku\":\"N\",\"name\":\"x\",\"price\":1,\"stock\":-1}"));
    }

    @Test
    void inStockFilter() throws Exception {
        String empty = product("1", 0);
        String full = product("1", 2);
        Set<String> skus = new HashSet<>();
        expect(200, call(get("/api/products").param("inStock", "true"))).forEach(n -> skus.add(n.get("sku").asText()));
        assertThat(skus).contains(full).doesNotContain(empty);
    }

    @Test
    void stockAdjustment() throws Exception {
        String sku = product("1", 3);
        assertThat(expect(200, send(patch("/api/products/" + sku + "/stock"), "{\"delta\":2}")).get("stock").asInt()).isEqualTo(5);
        expect(409, send(patch("/api/products/" + sku + "/stock"), "{\"delta\":-6}"));
        assertThat(stock(sku)).isEqualTo(5);
        expect(404, send(patch("/api/products/NOPE-404/stock"), "{\"delta\":1}"));
    }

    @Test
    void placeOrderComputesTotalsAndDecrementsStock() throws Exception {
        String a = product("2.50", 10);
        String b = product("0.10", 10);
        JsonNode placed = expect(201, order(a, "3", b, "3"));
        assertThat(placed.get("status").asText()).isEqualTo("PLACED");
        assertThat(placed.get("total").asDouble()).isCloseTo(7.8, org.assertj.core.data.Offset.offset(0.001));
        for (JsonNode line : placed.get("items")) {
            double expected = line.get("sku").asText().equals(a) ? 7.5 : 0.3;
            assertThat(line.get("lineTotal").asDouble()).isCloseTo(expected, org.assertj.core.data.Offset.offset(0.001));
        }
        assertThat(stock(a)).isEqualTo(7);
        expect(200, call(get("/api/orders/" + placed.get("id").asLong())));
        expect(404, call(get("/api/orders/999999")));
    }

    @Test
    void orderIsAllOrNothing() throws Exception {
        String a = product("1", 5);
        String b = product("1", 1);
        expect(409, order(a, "2", b, "2"));
        assertThat(stock(a)).isEqualTo(5);
        expect(404, order(a, "2", "NOPE-404", "1"));
        assertThat(stock(a)).isEqualTo(5);
    }

    @Test
    void orderValidation() throws Exception {
        String a = product("1", 5);
        expect(400, send(post("/api/orders"), "{\"items\":[]}"));
        expect(400, order(a, "0"));
    }

    @Test
    void cancelRestoresStock() throws Exception {
        String a = product("1", 4);
        long id = expect(201, order(a, "3")).get("id").asLong();
        assertThat(expect(200, call(post("/api/orders/" + id + "/cancel"))).get("status").asText()).isEqualTo("CANCELLED");
        assertThat(stock(a)).isEqualTo(4);
        expect(409, call(post("/api/orders/" + id + "/cancel")));
        expect(404, call(post("/api/orders/999999/cancel")));
    }
}
