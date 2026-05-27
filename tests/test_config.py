import pytest
from pydantic import ValidationError

from decant.config import Config, ConfigMissingError, load_config, save_config


def test_save_and_load_round_trip(tmp_path):
    cfg = Config(contact_email="alice@example.com")
    path = tmp_path / "config.yaml"
    save_config(cfg, path)
    loaded = load_config(path)
    assert loaded.contact_email == "alice@example.com"
    assert loaded.ollama.host == "http://localhost:11434"
    assert loaded.cache.ttl_hours == 24
    assert loaded.fetch.block_resources == ["image", "font", "media"]


def test_load_missing_raises(tmp_path):
    with pytest.raises(ConfigMissingError):
        load_config(tmp_path / "nope.yaml")


def test_defaults_fill_for_partial_yaml(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("contact_email: bob@example.com\n")
    cfg = load_config(path)
    assert cfg.ollama.model == "qwen3:32b"
    assert cfg.fetch.navigation_wait == "networkidle"


@pytest.mark.parametrize("bad", ["not-an-email", "alice@", "@example.com", "alice"])
def test_email_validation_rejects_garbage(bad):
    with pytest.raises(ValidationError):
        Config(contact_email=bad)
