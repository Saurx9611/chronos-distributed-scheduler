from chronos.api.main import create_app
from fastapi.testclient import TestClient


def test_liveness_contract() -> None:
    with TestClient(create_app()) as client:
        response = client.get("/health/live")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}


def test_openapi_contains_only_foundation_route() -> None:
    schema = create_app().openapi()
    assert set(schema["paths"]) == {"/health/live"}
    assert schema["paths"]["/health/live"]["get"]["responses"]["200"]
