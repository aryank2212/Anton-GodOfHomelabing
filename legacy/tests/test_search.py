"""Tests for search and knowledge endpoints."""

from fastapi.testclient import TestClient


def test_memory_search(client: TestClient, user_token):
    client.post(
        "/api/entries/",
        json={"title": "Search Test", "content": "Unique searchable content here"},
        cookies={"session_id": user_token},
    )

    response = client.get(
        "/api/memory/search?q=searchable",
        cookies={"session_id": user_token},
    )
    assert response.status_code == 200
    results = response.json()
    assert len(results) >= 1
    assert any("searchable" in r["content"] for r in results)


def test_knowledge_search(client: TestClient, user_token):
    response = client.get(
        "/api/knowledge/search?q=test",
        cookies={"session_id": user_token},
    )
    assert response.status_code == 200


def test_knowledge_context(client: TestClient, user_token):
    response = client.get(
        "/api/knowledge/context?topics=test",
        cookies={"session_id": user_token},
    )
    assert response.status_code == 200
    assert "context" in response.json()


def test_knowledge_context_required(client: TestClient, user_token):
    response = client.get(
        "/api/knowledge/context",
        cookies={"session_id": user_token},
    )
    assert response.status_code == 400


def test_rag(client: TestClient, user_token):
    response = client.get(
        "/api/knowledge/rag?query=test",
        cookies={"session_id": user_token},
    )
    assert response.status_code == 200
    assert "context" in response.json()


def test_memory_timeline(client: TestClient, user_token):
    response = client.get(
        "/api/memory/timeline",
        cookies={"session_id": user_token},
    )
    assert response.status_code == 200
