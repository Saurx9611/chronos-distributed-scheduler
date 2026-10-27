import pytest
from chronos.api.settings import ApiSettings
from pydantic import ValidationError


def test_bad_api_configuration_is_rejected_without_echoing_secret_inputs() -> None:
    supplied = "TEST_ONLY_PRIVATE_CONFIGURATION"
    with pytest.raises(ValidationError) as captured:
        ApiSettings.model_validate({"CHRONOS_API_AUTH_PEPPER": supplied})
    assert supplied not in str(captured.value)
    with pytest.raises(ValidationError):
        ApiSettings.model_validate({"CHRONOS_DATABASE_URL": "sqlite:///wrong-database"})
