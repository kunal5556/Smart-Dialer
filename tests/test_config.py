import pytest

from app.config import ConfigurationError, Settings, load_settings


def test_settings_load_from_environment(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MONGODB_URI", "mongodb://example:27017")
    monkeypatch.setenv("MONGODB_DB_NAME", "custom_db")
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")

    settings = load_settings()

    assert settings.MONGODB_URI == "mongodb://example:27017"
    assert settings.MONGODB_DB_NAME == "custom_db"
    assert settings.LOG_LEVEL == "DEBUG"


def test_missing_required_variable_raises_configuration_error(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("MONGODB_URI", raising=False)

    with pytest.raises(ConfigurationError) as error:
        load_settings()

    assert "MONGODB_URI" in str(error.value)


def test_tunable_defaults_match_the_roadmap(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MONGODB_URI", "mongodb://example:27017")

    settings = load_settings()

    assert settings.RESERVATION_TTL_SECONDS == 30
    assert settings.AGENT_HEARTBEAT_TIMEOUT_SECONDS == 30
    assert settings.CALL_STALE_TIMEOUT_SECONDS == 120
    assert settings.WRAP_UP_SECONDS == 10
    assert settings.MAX_CALL_ATTEMPTS == 3
    assert settings.RETRY_BACKOFF_BASE_SECONDS == 60
    assert settings.DIALER_TICK_SECONDS == 1.0
    assert settings.RECOVERY_TICK_SECONDS == 5.0
    assert settings.SAFETY_MARGIN == 0.85
    assert settings.MAX_RINGING_RATIO == 2.0
    assert settings.MAX_SNAPSHOT_AGE_SECONDS == 5.0
    assert settings.AVAILABILITY_DROP_THRESHOLD == 0.25


def test_cors_origins_are_split_into_a_list():
    settings = Settings(
        MONGODB_URI="mongodb://example:27017",
        CORS_ORIGINS="http://localhost:8501, https://dashboard.example.com",
    )

    assert settings.cors_origins == [
        "http://localhost:8501",
        "https://dashboard.example.com",
    ]
