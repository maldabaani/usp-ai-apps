import uuid

import pytest
from fastapi.testclient import TestClient


def product(client: TestClient, price: float = 2.5, stock: int = 10) -> dict:
    sku = f"SKU-{uuid.uuid4().hex[:8]}"
    body = {"sku": sku, "name": "Widget", "price": price, "stock": stock}
    response = client.post("/products", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def stock(client: TestClient, sku: str) -> int:
    return int(client.get(f"/products/{sku}").json()["stock"])


def order(client: TestClient, *items: tuple[str, int]) -> object:
    return client.post("/orders", json={"items": [{"sku": s, "quantity": q} for s, q in items]})


def test_create_and_get_product(client: TestClient) -> None:
    p = product(client, price=3.25, stock=4)
    assert p["price"] == pytest.approx(3.25)
    assert p["stock"] == 4
    assert client.get(f"/products/{p['sku']}").json()["name"] == "Widget"
    assert client.get("/products/NOPE-404").status_code == 404


def test_duplicate_sku_and_validation(client: TestClient) -> None:
    p = product(client)
    dup = {"sku": p["sku"], "name": "x", "price": 1, "stock": 1}
    assert client.post("/products", json=dup).status_code == 409
    assert client.post("/products", json={"sku": "N", "name": "x", "price": 0, "stock": 1}).status_code == 422
    assert client.post("/products", json={"sku": "N", "name": "x", "price": 1, "stock": -1}).status_code == 422


def test_in_stock_filter(client: TestClient) -> None:
    empty = product(client, stock=0)
    full = product(client, stock=2)
    skus = {p["sku"] for p in client.get("/products", params={"in_stock": "true"}).json()}
    assert full["sku"] in skus
    assert empty["sku"] not in skus
    all_skus = {p["sku"] for p in client.get("/products").json()}
    assert empty["sku"] in all_skus


def test_stock_adjustment(client: TestClient) -> None:
    p = product(client, stock=3)
    response = client.patch(f"/products/{p['sku']}/stock", json={"delta": 2})
    assert response.status_code == 200
    assert response.json()["stock"] == 5
    assert client.patch(f"/products/{p['sku']}/stock", json={"delta": -6}).status_code == 409
    assert stock(client, p["sku"]) == 5
    assert client.patch("/products/NOPE-404/stock", json={"delta": 1}).status_code == 404


def test_place_order_totals_and_stock(client: TestClient) -> None:
    a = product(client, price=2.5, stock=10)
    b = product(client, price=0.1, stock=10)
    response = order(client, (a["sku"], 3), (b["sku"], 3))
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "placed"
    lines = {line["sku"]: line for line in body["items"]}
    assert lines[a["sku"]]["unit_price"] == pytest.approx(2.5)
    assert lines[a["sku"]]["line_total"] == pytest.approx(7.5)
    assert lines[b["sku"]]["line_total"] == pytest.approx(0.3)
    assert body["total"] == pytest.approx(7.8)
    assert stock(client, a["sku"]) == 7
    assert client.get(f"/orders/{body['id']}").json()["total"] == pytest.approx(7.8)


def test_order_is_all_or_nothing(client: TestClient) -> None:
    a = product(client, stock=5)
    b = product(client, stock=1)
    assert order(client, (a["sku"], 2), (b["sku"], 2)).status_code == 409
    assert stock(client, a["sku"]) == 5
    assert order(client, (a["sku"], 2), ("NOPE-404", 1)).status_code == 404
    assert stock(client, a["sku"]) == 5


def test_order_validation(client: TestClient) -> None:
    a = product(client)
    assert client.post("/orders", json={"items": []}).status_code == 422
    assert order(client, (a["sku"], 0)).status_code == 422
    assert client.get("/orders/999999").status_code == 404


def test_cancel_restores_stock(client: TestClient) -> None:
    a = product(client, stock=4)
    placed = order(client, (a["sku"], 3)).json()
    response = client.post(f"/orders/{placed['id']}/cancel")
    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"
    assert stock(client, a["sku"]) == 4
    assert client.post(f"/orders/{placed['id']}/cancel").status_code == 409
    assert client.post("/orders/999999/cancel").status_code == 404
