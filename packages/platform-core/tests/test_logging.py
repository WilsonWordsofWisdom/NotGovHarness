import json

from platform_core.logging import configure_logging, get_logger


def test_emits_json(capsys):
    configure_logging("info")
    get_logger("test").info("hello", widget=7)
    out = capsys.readouterr().out.strip().splitlines()[-1]
    record = json.loads(out)
    assert record["event"] == "hello"
    assert record["widget"] == 7
    assert record["level"] == "info"
    assert "timestamp" in record
