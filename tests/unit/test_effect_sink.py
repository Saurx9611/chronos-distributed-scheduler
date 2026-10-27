import hashlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

from chronos.demo_sink import create_sink
from fastapi.testclient import TestClient


def test_destination_deduplication_is_transactional(tmp_path: Path) -> None:
    token = "test-destination-token"
    with TestClient(create_sink(tmp_path / "sink.sqlite", token)) as client:
        key = str(uuid4())
        body = {"effect_key": key, "fingerprint": hashlib.sha256(b"value").hexdigest()}
        headers = {"Authorization": "Bearer " + token}
        assert client.post("/effects", json=body).status_code == 401

        def deliver(_: int) -> int:
            response = client.post("/effects", json=body, headers=headers)
            assert response.status_code == 200
            return int(response.json()["effect_id"])

        with ThreadPoolExecutor(max_workers=4) as executor:
            assert len(set(executor.map(deliver, range(4)))) == 1
        assert client.get(f"/stats/{key}", headers=headers).json() == {"effects": 1, "requests": 4}
        conflict = body | {"fingerprint": hashlib.sha256(b"different").hexdigest()}
        assert client.post("/effects", json=conflict, headers=headers).status_code == 409
        assert client.post("/unsafe-effects", json=body, headers=headers).status_code == 403
        assert (
            client.post(
                f"/gates/{key}/arrive",
                json={"generation": 1, "stage": "before_ack"},
                headers=headers,
            ).status_code
            == 403
        )


def test_unconfigured_destination_fails_closed(tmp_path: Path) -> None:
    with TestClient(create_sink(tmp_path / "disabled.sqlite", None)) as client:
        response = client.post(
            "/effects",
            json={"effect_key": str(uuid4()), "fingerprint": "a" * 64},
            headers={"Authorization": "Bearer test"},
        )
        assert response.status_code == 503
        assert not (tmp_path / "disabled.sqlite").exists()
