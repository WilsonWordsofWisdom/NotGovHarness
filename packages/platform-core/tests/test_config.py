from platform_core.config import PlatformSettings


def test_defaults():
    s = PlatformSettings(service_name="svc")
    assert s.service_name == "svc"
    assert s.env == "dev"
    assert s.log_level == "info"
    assert s.auth_mode == "dev"
    assert s.kafka_brokers == "localhost:9092"
    assert s.database_url is None
    assert s.otel_exporter_otlp_endpoint is None


def test_env_override(monkeypatch):
    monkeypatch.setenv("LOG_LEVEL", "debug")
    monkeypatch.setenv("KAFKA_BROKERS", "broker:9092")
    s = PlatformSettings(service_name="svc")
    assert s.log_level == "debug"
    assert s.kafka_brokers == "broker:9092"


def test_subclass_adds_keys(monkeypatch):
    class ServiceSettings(PlatformSettings):
        widget_limit: int = 5

    monkeypatch.setenv("WIDGET_LIMIT", "9")
    s = ServiceSettings(service_name="svc")
    assert s.widget_limit == 9
