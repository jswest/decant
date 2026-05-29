import pytest
from pydantic import ValidationError

from decant.config import OllamaConfig, Config, ConfigMissingError, load_config, save_config


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
    assert cfg.ollama.fast_model is None
    assert cfg.fetch.navigation_wait == "networkidle"


def test_fast_model_round_trips(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        "contact_email: c@d.co\n"
        "ollama:\n  model: qwen3:32b\n  fast_model: qwen3:8b\n"
    )
    cfg = load_config(path)
    assert cfg.ollama.fast_model == "qwen3:8b"


@pytest.mark.parametrize("bad", ["not-an-email", "alice@", "@example.com", "alice"])
def test_email_validation_rejects_garbage(bad):
    with pytest.raises(ValidationError):
        Config(contact_email=bad)


def test_search_config_defaults():
    from decant.config import OllamaConfig, SearchConfig

    assert Config(contact_email="a@b.co").search == SearchConfig()


@pytest.mark.parametrize(
    "yaml_key,env_value,expected",
    [
        ("from-file", "from-env", "from-env"),   # env overrides file
        (None,        "from-env", "from-env"),   # env fills when file absent
        ("from-file", None,       "from-file"),  # no env → file wins
    ],
    ids=["env_overrides_file", "env_fills_empty", "file_alone"],
)
def test_brave_api_key_resolution(tmp_path, monkeypatch, yaml_key, env_value, expected):
    path = tmp_path / "config.yaml"
    yaml_doc = "contact_email: a@b.co\n"
    if yaml_key is not None:
        yaml_doc += f"search:\n  brave_api_key: {yaml_key}\n"
    path.write_text(yaml_doc)

    if env_value is None:
        monkeypatch.delenv("BRAVE_SEARCH_API_KEY", raising=False)
    else:
        monkeypatch.setenv("BRAVE_SEARCH_API_KEY", env_value)

    assert load_config(path).search.brave_api_key == expected


def test_ollama_config_default_keep_alive():
    """Default keep_alive should be 5m."""
    cfg = OllamaConfig()
    assert cfg.keep_alive == "5m"


def test_ollama_config_valid_keep_alive():
    """Valid keep_alive values should be accepted."""
    for val in ["5m", "1h", "30s", "500ms", "0", "-1"]:
        cfg = OllamaConfig(keep_alive=val)
        assert cfg.keep_alive == val


def test_ollama_config_invalid_keep_alive():
    """Invalid keep_alive values should raise ValueError."""
    import pytest as pt
    for val in ["abc", "5x", "1.5m", ""]:
        with pt.raises(Exception):
            OllamaConfig(keep_alive=val)
