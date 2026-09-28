import uuid

from fastapi.testclient import TestClient


def uid() -> str:
    return uuid.uuid4().hex[:8]


def create(client: TestClient, **body: object) -> dict:
    response = client.post("/notes", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def test_create_with_defaults(client: TestClient) -> None:
    note = create(client, title="plain")
    assert isinstance(note["id"], int)
    assert note["body"] == ""
    assert note["tags"] == []


def test_tags_are_normalized(client: TestClient) -> None:
    note = create(client, title="t", tags=[" Work ", "home", "WORK", "", "art"])
    assert note["tags"] == ["art", "home", "work"]


def test_validation(client: TestClient) -> None:
    assert client.post("/notes", json={"title": ""}).status_code == 422
    assert client.post("/notes", json={"title": "x" * 121}).status_code == 422
    assert client.post("/notes", json={"body": "no title"}).status_code == 422


def test_filter_by_tag_case_insensitive(client: TestClient) -> None:
    tag = f"tag{uid()}"
    tagged = create(client, title="a", tags=[tag])
    create(client, title="b", tags=["other"])
    response = client.get("/notes", params={"tag": tag.upper()})
    assert response.status_code == 200
    assert [n["id"] for n in response.json()] == [tagged["id"]]


def test_search_title_or_body(client: TestClient) -> None:
    word = f"needle{uid()}"
    in_title = create(client, title=f"has {word.upper()}")
    in_body = create(client, title="x", body=f"the {word} is here")
    create(client, title="unrelated")
    ids = {n["id"] for n in client.get("/notes", params={"q": word}).json()}
    assert ids == {in_title["id"], in_body["id"]}


def test_tag_and_query_combine(client: TestClient) -> None:
    tag, word = f"tag{uid()}", f"w{uid()}"
    both = create(client, title=word, tags=[tag])
    create(client, title=word)
    create(client, title="nope", tags=[tag])
    result = client.get("/notes", params={"tag": tag, "q": word}).json()
    assert [n["id"] for n in result] == [both["id"]]


def test_patch_updates_only_given_fields(client: TestClient) -> None:
    note = create(client, title="orig", body="body", tags=["a"])
    response = client.patch(f"/notes/{note['id']}", json={"tags": ["B", "b"]})
    assert response.status_code == 200
    assert response.json() == {**note, "tags": ["b"]}
    response = client.patch(f"/notes/{note['id']}", json={"title": "new"})
    assert response.json()["body"] == "body"
    assert response.json()["title"] == "new"


def test_patch_missing_and_invalid(client: TestClient) -> None:
    assert client.patch("/notes/999999", json={"title": "x"}).status_code == 404
    note = create(client, title="ok")
    assert client.patch(f"/notes/{note['id']}", json={"title": ""}).status_code == 422


def test_get_and_delete(client: TestClient) -> None:
    note = create(client, title="bye")
    assert client.get(f"/notes/{note['id']}").json() == note
    assert client.delete(f"/notes/{note['id']}").status_code == 204
    assert client.get(f"/notes/{note['id']}").status_code == 404
    assert client.delete(f"/notes/{note['id']}").status_code == 404


def test_tag_counts(client: TestClient) -> None:
    a, b = f"a{uid()}", f"b{uid()}"
    create(client, title="1", tags=[a, b])
    second = create(client, title="2", tags=[a])
    counts = {t["tag"]: t["count"] for t in client.get("/tags").json()}
    assert counts[a] == 2
    assert counts[b] == 1
    client.delete(f"/notes/{second['id']}")
    counts = {t["tag"]: t["count"] for t in client.get("/tags").json()}
    assert counts[a] == 1


def test_tags_sorted(client: TestClient) -> None:
    create(client, title="s", tags=[f"zz{uid()}", f"aa{uid()}"])
    tags = [t["tag"] for t in client.get("/tags").json()]
    assert tags == sorted(tags)
