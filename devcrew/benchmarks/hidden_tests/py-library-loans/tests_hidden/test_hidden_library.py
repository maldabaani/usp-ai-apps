from fastapi.testclient import TestClient


def post(client: TestClient, path: str, body: dict, status: int = 201) -> dict:
    response = client.post(path, json=body)
    assert response.status_code == status, response.text
    return response.json()


def book(client: TestClient, copies: int = 1) -> dict:
    author = post(client, "/authors", {"name": "Ursula"})
    return post(client, "/books", {"title": "Earthsea", "author_id": author["id"], "copies": copies})


def test_author_crud(client: TestClient) -> None:
    author = post(client, "/authors", {"name": "Le Guin"})
    assert author["name"] == "Le Guin"
    assert client.get(f"/authors/{author['id']}").json() == author
    assert client.get("/authors/999999").status_code == 404
    assert client.post("/authors", json={"name": ""}).status_code == 422


def test_book_starts_fully_available(client: TestClient) -> None:
    created = book(client, copies=3)
    assert created["copies"] == 3
    assert created["available"] == 3
    assert client.get(f"/books/{created['id']}").json()["available"] == 3


def test_book_validation(client: TestClient) -> None:
    author = post(client, "/authors", {"name": "A"})
    assert client.post("/books", json={"title": "t", "author_id": 999999, "copies": 1}).status_code == 404
    bad = {"title": "t", "author_id": author["id"], "copies": 0}
    assert client.post("/books", json=bad).status_code == 422
    assert client.get("/books/999999").status_code == 404


def test_author_books(client: TestClient) -> None:
    author = post(client, "/authors", {"name": "B"})
    b1 = post(client, "/books", {"title": "one", "author_id": author["id"], "copies": 1})
    b2 = post(client, "/books", {"title": "two", "author_id": author["id"], "copies": 2})
    listed = client.get(f"/authors/{author['id']}/books").json()
    assert {b["id"] for b in listed} == {b1["id"], b2["id"]}
    assert client.get("/authors/999999/books").status_code == 404


def test_loan_reduces_availability_and_conflicts_when_none_left(client: TestClient) -> None:
    created = book(client, copies=1)
    loan = post(client, "/loans", {"book_id": created["id"], "borrower": "sam"})
    assert loan["returned"] is False
    assert loan["book_id"] == created["id"]
    assert client.get(f"/books/{created['id']}").json()["available"] == 0
    post(client, "/loans", {"book_id": created["id"], "borrower": "kim"}, status=409)


def test_loan_unknown_book(client: TestClient) -> None:
    post(client, "/loans", {"book_id": 999999, "borrower": "x"}, status=404)


def test_return_restores_copy_and_twice_conflicts(client: TestClient) -> None:
    created = book(client, copies=1)
    loan = post(client, "/loans", {"book_id": created["id"], "borrower": "sam"})
    returned = client.post(f"/loans/{loan['id']}/return")
    assert returned.status_code == 200
    assert returned.json()["returned"] is True
    assert client.get(f"/books/{created['id']}").json()["available"] == 1
    assert client.post(f"/loans/{loan['id']}/return").status_code == 409
    assert client.post("/loans/999999/return").status_code == 404


def test_active_loans_filter(client: TestClient) -> None:
    created = book(client, copies=2)
    active = post(client, "/loans", {"book_id": created["id"], "borrower": "a"})
    done = post(client, "/loans", {"book_id": created["id"], "borrower": "b"})
    client.post(f"/loans/{done['id']}/return")
    active_ids = {l["id"] for l in client.get("/loans", params={"active": "true"}).json()}
    all_ids = {l["id"] for l in client.get("/loans").json()}
    assert active["id"] in active_ids
    assert done["id"] not in active_ids
    assert {active["id"], done["id"]} <= all_ids
