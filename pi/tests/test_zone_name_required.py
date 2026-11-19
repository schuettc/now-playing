"""SONOS_ZONE_NAME has no default: a missing name must stop the service
with a message that names the variable."""
import pytest

from nowplaying.sonos import listener


def test_missing_zone_name_exits_naming_the_variable(monkeypatch):
    monkeypatch.delenv("SONOS_ZONE_NAME", raising=False)
    with pytest.raises(SystemExit) as exc:
        listener.get_zone_name()
    assert "SONOS_ZONE_NAME" in str(exc.value)


def test_blank_zone_name_exits(monkeypatch):
    monkeypatch.setenv("SONOS_ZONE_NAME", "  ")
    with pytest.raises(SystemExit):
        listener.get_zone_name()


def test_zone_name_is_read_from_the_environment(monkeypatch):
    monkeypatch.setenv("SONOS_ZONE_NAME", "Living Room")
    assert listener.get_zone_name() == "Living Room"
