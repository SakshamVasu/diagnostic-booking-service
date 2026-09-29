from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.helpers import Headers


def test_admin_can_create_centre(client: TestClient, admin_headers: Headers) -> None:
    response = client.post("/centres/", json={"name": "Apollo Diagnostics", "location": "Delhi"}, headers=admin_headers)

    assert response.status_code == 201
    body = response.json()
    assert body["id"] > 0
    assert body["name"] == "Apollo Diagnostics"
    assert body["location"] == "Delhi"
    assert body["created_at"] and body["updated_at"]


def test_create_centre_validates_input(client: TestClient, admin_headers: Headers) -> None:
    response = client.post("/centres/", json={"name": "   ", "location": "Delhi"}, headers=admin_headers)
    assert response.status_code == 422


def test_duplicate_centre_at_same_location_conflicts(
    client: TestClient, admin_headers: Headers, create_centre: Callable[..., dict[str, Any]]
) -> None:
    create_centre()
    response = client.post("/centres/", json={"name": "Apollo Diagnostics", "location": "Delhi"}, headers=admin_headers)
    assert response.status_code == 409


def test_get_centre(client: TestClient, create_centre: Callable[..., dict[str, Any]]) -> None:
    centre = create_centre()

    response = client.get(f"/centres/{centre['id']}")

    assert response.status_code == 200
    assert response.json() == centre


def test_list_centres_paginated(client: TestClient, create_centre: Callable[..., dict[str, Any]]) -> None:
    for i in range(5):
        create_centre(name=f"Centre {i}")

    response = client.get("/centres/?page=2&page_size=2")

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 5
    assert body["page"] == 2
    assert body["page_size"] == 2
    assert body["pages"] == 3
    assert [c["name"] for c in body["items"]] == ["Centre 2", "Centre 3"]


@pytest.mark.parametrize("query", ["page=0", "page_size=0", "page_size=101", "page=abc"])
def test_list_centres_rejects_invalid_pagination(client: TestClient, query: str) -> None:
    assert client.get(f"/centres/?{query}").status_code == 422


def test_list_centres_filters_by_location_and_name(
    client: TestClient, create_centre: Callable[..., dict[str, Any]]
) -> None:
    create_centre(name="Apollo Diagnostics", location="Delhi")
    create_centre(name="Apollo Diagnostics", location="Mumbai")
    create_centre(name="Metro Labs", location="New Delhi")

    by_location = client.get("/centres/?location=delhi").json()
    by_name = client.get("/centres/?search=apollo").json()
    wildcard = client.get("/centres/?search=%25").json()

    assert {c["location"] for c in by_location["items"]} == {"Delhi", "New Delhi"}
    assert {c["location"] for c in by_name["items"]} == {"Delhi", "Mumbai"}
    assert wildcard["total"] == 0  # LIKE wildcards are matched literally


def test_admin_can_update_centre(
    client: TestClient, admin_headers: Headers, create_centre: Callable[..., dict[str, Any]]
) -> None:
    centre = create_centre()

    response = client.patch(f"/centres/{centre['id']}", json={"location": "Gurugram"}, headers=admin_headers)

    assert response.status_code == 200
    assert response.json()["location"] == "Gurugram"
    assert response.json()["name"] == centre["name"]


def test_update_centre_rejects_null_fields(
    client: TestClient, admin_headers: Headers, create_centre: Callable[..., dict[str, Any]]
) -> None:
    centre = create_centre()
    response = client.patch(f"/centres/{centre['id']}", json={"name": None}, headers=admin_headers)
    assert response.status_code == 422


def test_admin_can_delete_centre(
    client: TestClient, admin_headers: Headers, create_centre: Callable[..., dict[str, Any]]
) -> None:
    centre = create_centre()

    assert client.delete(f"/centres/{centre['id']}", headers=admin_headers).status_code == 204
    assert client.get(f"/centres/{centre['id']}").status_code == 404


def test_centre_with_bookings_cannot_be_deleted(
    client: TestClient, admin_headers: Headers, create_booking: Callable[..., dict[str, Any]]
) -> None:
    booking = create_booking()

    response = client.delete(f"/centres/{booking['centre_id']}", headers=admin_headers)

    assert response.status_code == 409


@pytest.mark.parametrize(("centre_id", "status"), [(999_999, 404), (0, 422), ("abc", 422)])
def test_invalid_centre_id(client: TestClient, admin_headers: Headers, centre_id: object, status: int) -> None:
    assert client.get(f"/centres/{centre_id}").status_code == status
    assert client.patch(f"/centres/{centre_id}", json={"name": "X"}, headers=admin_headers).status_code == status
    assert client.delete(f"/centres/{centre_id}", headers=admin_headers).status_code == status


def test_not_found_error_shape(client: TestClient) -> None:
    assert client.get("/centres/999999").json() == {"detail": "Centre not found"}


def test_regular_user_cannot_modify_centres(
    client: TestClient, user_headers: Headers, create_centre: Callable[..., dict[str, Any]]
) -> None:
    centre = create_centre()

    create = client.post("/centres/", json={"name": "X", "location": "Y"}, headers=user_headers)
    update = client.patch(f"/centres/{centre['id']}", json={"name": "Hacked"}, headers=user_headers)
    delete = client.delete(f"/centres/{centre['id']}", headers=user_headers)
    offer = client.post(f"/centres/{centre['id']}/tests", json={"test_id": 1}, headers=user_headers)

    assert [r.status_code for r in (create, update, delete, offer)] == [403, 403, 403, 403]
    assert create.json() == {"detail": "Admin privileges required"}
    assert client.get(f"/centres/{centre['id']}").json()["name"] == centre["name"]


def test_anonymous_user_cannot_modify_centres(client: TestClient) -> None:
    assert client.post("/centres/", json={"name": "X", "location": "Y"}).status_code == 401
