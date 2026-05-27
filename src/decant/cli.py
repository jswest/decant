from __future__ import annotations

import json

import click
import httpx

from decant import __version__, cache as cache_mod
from decant.config import (
    CACHE_DIR,
    CONFIG_PATH,
    EMAIL_RE,
    Config,
    ConfigMissingError,
    load_config,
    save_config,
)


@click.group()
def main() -> None:
    """decant — turn URLs into clean markdown and structured findings."""


@main.command("config")
def config_cmd() -> None:
    """Interactive first-run setup. Writes ~/.decant/config.yaml."""
    try:
        existing = load_config()
    except ConfigMissingError:
        existing = None

    email = _prompt_email(existing.contact_email if existing else None)
    host = click.prompt(
        "Ollama host",
        default=existing.ollama.host if existing else "http://localhost:11434",
    )
    model = _prompt_model(host, existing.ollama.model if existing else None)
    ttl = click.prompt(
        "Cache TTL hours",
        default=existing.cache.ttl_hours if existing else 24,
        type=int,
    )

    cfg = Config.model_validate(
        {
            "contact_email": email,
            "ollama": {"host": host, "model": model},
            "cache": {"ttl_hours": ttl},
        }
    )
    save_config(cfg)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    click.echo(f"Wrote {CONFIG_PATH}")


def _prompt_email(default: str | None) -> str:
    while True:
        email = click.prompt("Contact email", default=default)
        if EMAIL_RE.match(email):
            return email
        click.echo("Not a plausible email; try again.", err=True)


def _prompt_model(host: str, default: str | None) -> str:
    models = _list_ollama_models(host)
    if models:
        click.echo(f"Available models on {host}: {', '.join(models)}")
        default = default if default in models else models[0]
    else:
        click.echo(f"(Could not reach {host}/api/tags; prompting blindly.)", err=True)
    return click.prompt("Ollama model", default=default)


def _list_ollama_models(host: str) -> list[str]:
    try:
        r = httpx.get(f"{host.rstrip('/')}/api/tags", timeout=5)
        r.raise_for_status()
        return [m["name"] for m in r.json().get("models", [])]
    except (httpx.HTTPError, ValueError, KeyError):
        return []


@main.group("cache")
def cache_group() -> None:
    """Inspect or clear ~/.decant/cache/."""


@cache_group.command("clear")
def cache_clear_cmd() -> None:
    """Delete cache contents. Prints {cleared, freed_bytes}."""
    cleared, freed = cache_mod.clear(CACHE_DIR)
    click.echo(json.dumps({"cleared": cleared, "freed_bytes": freed}))


@cache_group.command("stats")
def cache_stats_cmd() -> None:
    """Print {entries, total_bytes, oldest, newest} for the cache."""
    click.echo(json.dumps(cache_mod.stats(CACHE_DIR)))


@main.command("version")
def version_cmd() -> None:
    """Print version, config path, and cache dir as JSON."""
    payload = {
        "version": __version__,
        "config_path": str(CONFIG_PATH),
        "cache_dir": str(CACHE_DIR),
    }
    click.echo(json.dumps(payload))


if __name__ == "__main__":
    main()
