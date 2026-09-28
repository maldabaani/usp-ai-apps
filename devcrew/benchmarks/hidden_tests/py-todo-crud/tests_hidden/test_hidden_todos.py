from fastapi.testclient import TestClient


def create(client: TestClient, title: str, **extra: object) -> dict:
    response = client.post("/todos", json={"title": title, **extra})
    assert response.status_code == 201, response.text
    return response.json()


def test_create_defaults_done_to_false(client: TestClient) -> None:
    todo = create(client, "buy milk")
    assert isinstance(todo["id"], int)
    assert todo["title"] == "buy milk"
    assert todo["done"] is False


def test_create_rejects_empty_and_too_long_titles(client: TestClient) -> None:
    assert client.post("/todos", json={"title": ""}).status_code == 422
    assert client.post("/todos", json={"title": "x" * 201}).status_code == 422
    assert client.post("/todos", json={}).status_code == 422


def test_list_contains_created_todos(client: TestClient) -> None:
    a = create(client, "list a")
    b = create(client, "list b", done=True)
    response = client.get("/todos")
    assert response.status_code == 200
    ids = {t["id"] for t in response.json()}
    assert {a["id"], b["id"]} <= ids


def test_get_one_and_404(client: TestClient) -> None:
    todo = create(client, "get me")
    assert client.get(f"/todos/{todo['id']}").json() == todo
    assert client.get("/todos/999999").status_code == 404


def test_put_replaces_todo(client: TestClient) -> None:
    todo = create(client, "old")
    response = client.put(f"/todos/{todo['id']}", json={"title": "new", "done": True})
    assert response.status_code == 200
    assert response.json() == {"id": todo["id"], "title": "new", "done": True}
    assert client.get(f"/todos/{todo['id']}").json()["done"] is True


def test_put_missing_and_invalid(client: TestClient) -> None:
    assert client.put("/todos/999999", json={"title": "x", "done": False}).status_code == 404
    todo = create(client, "valid")
    assert client.put(f"/todos/{todo['id']}", json={"title": "", "done": False}).status_code == 422


def test_delete_then_gone(client: TestClient) -> None:
    todo = create(client, "delete me")
    response = client.delete(f"/todos/{todo['id']}")
    assert response.status_code == 204
    assert response.content == b""
    assert client.get(f"/todos/{todo['id']}").status_code == 404
    assert client.delete(f"/todos/{todo['id']}").status_code == 404


def test_ids_are_unique_and_not_reused(client: TestClient) -> None:
    first = create(client, "first")
    client.delete(f"/todos/{first['id']}")
    second = create(client, "second")
    assert second["id"] != first["id"]
    assert second["id"] > 0


def test_health_still_works(client: TestClient) -> None:
    assert client.get("/health").status_code == 200
