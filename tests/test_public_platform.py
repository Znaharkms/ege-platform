from fastapi.testclient import TestClient

from app.main import app


def test_public_routes_and_assets_are_available_without_login():
    with TestClient(app) as client:
        for path in (
            "/", "/history", "/history/dates", "/history/dates/", "/history/terms",
            "/profile", "/profile/", "/society", "/society/terms", "/society/plans", "/search",
        ):
            response = client.get(path)
            assert response.status_code == 200, path
            assert 'id="material"' in response.text
        for path in (
            "/learn/assets/public.js", "/learn/assets/public.css", "/learn/assets/account.js",
            "/learn/assets/dashboard.js", "/learn/assets/mode-switch.js",
            "/learn/assets/mode-switch.css",
        ):
            assert client.get(path).status_code == 200
