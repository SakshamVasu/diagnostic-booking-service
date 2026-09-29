from fastapi.testclient import TestClient


def test_root_redirects_to_ui(client: TestClient) -> None:
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == "/ui/"


def test_ui_assets_are_served(client: TestClient) -> None:
    index = client.get("/ui/")
    assert index.status_code == 200
    assert "text/html" in index.headers["content-type"]
    assert 'src="app.js"' in index.text
    for asset in ("app.js", "api.js", "ui.js", "store.js", "booking.js", "admin.js", "theme.js", "styles.css"):
        assert client.get(f"/ui/{asset}").status_code == 200, asset


def test_ui_does_not_shadow_the_api(client: TestClient) -> None:
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/openapi.json").status_code == 200
