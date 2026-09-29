"""Diagnostic test catalogue and the centre <-> test association with per-centre prices."""

from collections.abc import Callable
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.helpers import Headers


def test_admin_can_create_test(client: TestClient, admin_headers: Headers) -> None:
    response = client.post(
        "/tests/",
        json={"name": "CBC", "description": "Complete blood count", "base_price": "499.99"},
        headers=admin_headers,
    )

    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "CBC"
    assert body["description"] == "Complete blood count"
    assert Decimal(body["base_price"]) == Decimal("499.99")


@pytest.mark.parametrize("base_price", ["0", "-10", "10.999", "abc", "12345678901"])
def test_create_test_rejects_invalid_price(client: TestClient, admin_headers: Headers, base_price: str) -> None:
    response = client.post("/tests/", json={"name": "CBC", "base_price": base_price}, headers=admin_headers)
    assert response.status_code == 422


def test_duplicate_test_name_conflicts(
    client: TestClient, admin_headers: Headers, create_test: Callable[..., dict[str, Any]]
) -> None:
    create_test(name="CBC")
    response = client.post("/tests/", json={"name": "CBC", "base_price": "100"}, headers=admin_headers)
    assert response.status_code == 409


def test_regular_user_cannot_create_or_modify_tests(
    client: TestClient, user_headers: Headers, create_test: Callable[..., dict[str, Any]]
) -> None:
    test = create_test()
    assert client.post("/tests/", json={"name": "X", "base_price": "1"}, headers=user_headers).status_code == 403
    assert client.patch(f"/tests/{test['id']}", json={"name": "X"}, headers=user_headers).status_code == 403
    assert client.delete(f"/tests/{test['id']}", headers=user_headers).status_code == 403


def test_get_test(client: TestClient, create_test: Callable[..., dict[str, Any]]) -> None:
    test = create_test()
    response = client.get(f"/tests/{test['id']}")
    assert response.status_code == 200
    assert response.json() == test


def test_list_tests_with_search_and_pagination(client: TestClient, create_test: Callable[..., dict[str, Any]]) -> None:
    create_test(name="Complete Blood Count")
    create_test(name="Glucose Fasting", description="Measures blood sugar")
    create_test(name="Lipid Profile", description="Cholesterol panel")

    everything = client.get("/tests/").json()
    blood = client.get("/tests/?search=BLOOD&page_size=1").json()

    assert everything["total"] == 3
    assert blood["total"] == 2
    assert len(blood["items"]) == 1
    assert blood["pages"] == 2


def test_update_and_delete_test(
    client: TestClient, admin_headers: Headers, create_test: Callable[..., dict[str, Any]]
) -> None:
    test = create_test()

    updated = client.patch(f"/tests/{test['id']}", json={"base_price": "650.50"}, headers=admin_headers)
    deleted = client.delete(f"/tests/{test['id']}", headers=admin_headers)

    assert updated.status_code == 200
    assert updated.json()["base_price"] == "650.50"
    assert deleted.status_code == 204
    assert client.get(f"/tests/{test['id']}").status_code == 404


def test_test_with_bookings_cannot_be_deleted(
    client: TestClient, admin_headers: Headers, create_booking: Callable[..., dict[str, Any]]
) -> None:
    booking = create_booking()
    assert client.delete(f"/tests/{booking['test_id']}", headers=admin_headers).status_code == 409


@pytest.mark.parametrize(("test_id", "status"), [(999_999, 404), (-1, 422), ("abc", 422)])
def test_invalid_test_id(client: TestClient, test_id: object, status: int) -> None:
    response = client.get(f"/tests/{test_id}")
    assert response.status_code == status


def test_associate_test_with_centre(
    client: TestClient,
    create_centre: Callable[..., dict[str, Any]],
    create_test: Callable[..., dict[str, Any]],
    offer_test: Callable[..., dict[str, Any]],
) -> None:
    centre = create_centre()
    test = create_test(base_price="500.00")

    offering = offer_test(centre["id"], test["id"], price="550.00")
    listed = client.get(f"/centres/{centre['id']}/tests").json()

    assert offering["centre_id"] == centre["id"]
    assert offering["test_id"] == test["id"]
    assert offering["test_name"] == test["name"]
    assert offering["price"] == "550.00"
    assert listed["total"] == 1
    assert listed["items"][0]["price"] == "550.00"


def test_association_defaults_to_base_price(
    create_centre: Callable[..., dict[str, Any]],
    create_test: Callable[..., dict[str, Any]],
    offer_test: Callable[..., dict[str, Any]],
) -> None:
    centre = create_centre()
    test = create_test(base_price="321.00")
    assert offer_test(centre["id"], test["id"])["price"] == "321.00"


def test_same_test_has_different_prices_at_different_centres(
    client: TestClient,
    create_centre: Callable[..., dict[str, Any]],
    create_test: Callable[..., dict[str, Any]],
    offer_test: Callable[..., dict[str, Any]],
) -> None:
    centre_a = create_centre(name="Centre A")
    centre_b = create_centre(name="Centre B")
    cbc = create_test(name="CBC")

    offer_test(centre_a["id"], cbc["id"], price="500.00")
    offer_test(centre_b["id"], cbc["id"], price="650.00")

    offerings = client.get(f"/tests/{cbc['id']}/centres").json()["items"]
    assert [(o["centre_name"], o["price"]) for o in offerings] == [("Centre A", "500.00"), ("Centre B", "650.00")]
    # Still exactly one test record: prices live on the association, not duplicated tests.
    assert client.get("/tests/").json()["total"] == 1


def test_duplicate_association_conflicts(
    client: TestClient,
    admin_headers: Headers,
    create_centre: Callable[..., dict[str, Any]],
    create_test: Callable[..., dict[str, Any]],
    offer_test: Callable[..., dict[str, Any]],
) -> None:
    centre, test = create_centre(), create_test()
    offer_test(centre["id"], test["id"])

    response = client.post(f"/centres/{centre['id']}/tests", json={"test_id": test["id"]}, headers=admin_headers)

    assert response.status_code == 409


def test_association_with_unknown_test_or_centre(
    client: TestClient, admin_headers: Headers, create_centre: Callable[..., dict[str, Any]]
) -> None:
    centre = create_centre()
    assert (
        client.post(f"/centres/{centre['id']}/tests", json={"test_id": 999}, headers=admin_headers).status_code == 404
    )
    assert client.post("/centres/999/tests", json={"test_id": 1}, headers=admin_headers).status_code == 404


def test_update_and_remove_centre_price(client: TestClient, admin_headers: Headers, catalogue: dict[str, Any]) -> None:
    centre_id, test_id = catalogue["centre"]["id"], catalogue["test"]["id"]

    updated = client.patch(f"/centres/{centre_id}/tests/{test_id}", json={"price": "800.00"}, headers=admin_headers)
    removed = client.delete(f"/centres/{centre_id}/tests/{test_id}", headers=admin_headers)

    assert updated.status_code == 200
    assert updated.json()["price"] == "800.00"
    assert removed.status_code == 204
    assert client.get(f"/centres/{centre_id}/tests").json()["total"] == 0


def test_deleting_centre_removes_its_offerings_but_not_tests(
    client: TestClient, admin_headers: Headers, catalogue: dict[str, Any]
) -> None:
    client.delete(f"/centres/{catalogue['centre']['id']}", headers=admin_headers)

    assert client.get(f"/tests/{catalogue['test']['id']}").status_code == 200
    assert client.get(f"/tests/{catalogue['test']['id']}/centres").json()["total"] == 0


# --- Preparation details --------------------------------------------------------------


def test_test_preparation_details_round_trip(client: TestClient, admin_headers: Headers) -> None:
    body = {
        "name": "Lipid Profile",
        "base_price": "800.00",
        "sample_type": "BLOOD",
        "fasting_hours": 10,
        "preparation_instructions": "Fast for 10-12 hours; water is fine.",
        "report_turnaround_hours": 24,
    }

    created = client.post("/tests/", json=body, headers=admin_headers)

    assert created.status_code == 201, created.text
    fetched = client.get(f"/tests/{created.json()['id']}").json()
    for key in ("sample_type", "fasting_hours", "preparation_instructions", "report_turnaround_hours"):
        assert fetched[key] == body[key]


def test_preparation_details_are_optional(create_test: Callable[..., dict[str, Any]]) -> None:
    test = create_test()
    assert (test["sample_type"], test["fasting_hours"], test["preparation_instructions"]) == (None, None, None)


@pytest.mark.parametrize(
    "details",
    [{"sample_type": "SALIVA"}, {"fasting_hours": -1}, {"fasting_hours": 73}, {"report_turnaround_hours": 0}],
)
def test_invalid_preparation_details_are_rejected(
    client: TestClient, admin_headers: Headers, details: dict[str, Any]
) -> None:
    response = client.post("/tests/", json={"name": "X", "base_price": "10", **details}, headers=admin_headers)
    assert response.status_code == 422


def test_preparation_details_can_be_cleared_but_name_cannot(client: TestClient, admin_headers: Headers) -> None:
    test = client.post(
        "/tests/",
        json={"name": "HbA1c", "base_price": "550", "fasting_hours": 0, "sample_type": "BLOOD"},
        headers=admin_headers,
    ).json()

    cleared = client.patch(
        f"/tests/{test['id']}", json={"fasting_hours": None, "sample_type": None}, headers=admin_headers
    )

    assert cleared.status_code == 200
    assert (cleared.json()["fasting_hours"], cleared.json()["sample_type"]) == (None, None)
    assert client.patch(f"/tests/{test['id']}", json={"name": None}, headers=admin_headers).status_code == 422
