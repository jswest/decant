from __future__ import annotations

import asyncio
import json
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urlsplit

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
from decant.distill import (
    OllamaBadJsonError,
    OllamaTimeoutError,
    OllamaUnavailableError,
    distill,
)
from decant.extract import ExtractEmptyError, extract as extract_html
from decant.fetch import FetchFailedError, Fetcher
from decant.models import ErrorCode
from decant.robots import RobotsCache
from decant.ua import build_user_agent


@click.group()
@click.option("--verbose", is_flag=True, help="Emit per-stage timings on stderr.")
@click.pass_context
def main(ctx: click.Context, verbose: bool) -> None:
    """decant — turn URLs into clean markdown and structured findings."""
    ctx.ensure_object(dict)
    ctx.obj["verbose"] = verbose


# ---------------------------------------------------------------------------
# decant config
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# decant cache
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# decant version
# ---------------------------------------------------------------------------


@main.command("version")
def version_cmd() -> None:
    """Print version, config path, and cache dir as JSON."""
    payload = {
        "version": __version__,
        "config_path": str(CONFIG_PATH),
        "cache_dir": str(CACHE_DIR),
    }
    click.echo(json.dumps(payload))


# ---------------------------------------------------------------------------
# decant url — the main course
# ---------------------------------------------------------------------------


@main.command("url")
@click.argument("urls", nargs=-1, required=True)
@click.option("--question", default=None, help="Research question for summary mode.")
@click.option(
    "--mode",
    type=click.Choice(["extract", "summary", "both"]),
    default=None,
    help="Output mode. Defaults to extract (no question) or summary (with question).",
)
@click.option("--model", default=None, help="Override the configured Ollama model.")
@click.option("--no-cache", is_flag=True, help="Bypass cache read and write.")
@click.option(
    "--timeout",
    "timeout_override",
    type=int,
    default=None,
    help="Per-URL fetch timeout in seconds (defaults to fetch.request_timeout_s).",
)
@click.pass_context
def url_cmd(
    ctx: click.Context,
    urls: tuple[str, ...],
    question: str | None,
    mode: str | None,
    model: str | None,
    no_cache: bool,
    timeout_override: int | None,
) -> None:
    """Fetch one or more URLs sequentially and emit JSON."""
    try:
        cfg = load_config()
    except ConfigMissingError:
        _emit_config_missing_and_exit()

    opts = _RunOpts(
        cfg=cfg,
        mode=_resolve_mode(mode, question),
        question=question,
        model=model or cfg.ollama.model,
        fetch_timeout=timeout_override or cfg.fetch.request_timeout_s,
        no_cache=no_cache,
        verbose=ctx.obj.get("verbose", False),
        ua=build_user_agent(cfg.contact_email),
    )

    results = asyncio.run(_process_all(opts, urls))

    payload = results[0] if len(urls) == 1 else results
    click.echo(json.dumps(payload, indent=2))
    if any("error" in r for r in results):
        sys.exit(1)


def _resolve_mode(mode: str | None, question: str | None) -> str:
    if mode is None:
        return "summary" if question else "extract"
    if mode in ("summary", "both") and not question:
        raise click.UsageError(f"--mode {mode} requires --question")
    return mode


def _emit_config_missing_and_exit() -> None:
    click.echo(
        json.dumps(
            {
                "error": "No ~/.decant/config.yaml. Run `decant config` first.",
                "code": ErrorCode.CONFIG_MISSING.value,
                "details": {"config_path": str(CONFIG_PATH)},
            }
        )
    )
    sys.exit(1)


@dataclass(frozen=True)
class _RunOpts:
    """Run-wide options that are the same for every URL in a batch."""

    cfg: Config
    mode: str
    question: str | None
    model: str
    fetch_timeout: int
    no_cache: bool
    verbose: bool
    ua: str


async def _process_all(opts: _RunOpts, urls: tuple[str, ...]) -> list[dict]:
    robots = RobotsCache()
    async with (
        Fetcher(
            user_agent=opts.ua, blocked_resources=opts.cfg.fetch.block_resources
        ) as fetcher,
        httpx.AsyncClient() as ollama_client,
    ):
        out: list[dict] = []
        for u in urls:
            if opts.verbose:
                click.echo(f"Fetching {u}", err=True)
            out.append(await _process_one(u, opts, robots, fetcher, ollama_client))
    return out


async def _process_one(
    url: str,
    opts: _RunOpts,
    robots: RobotsCache,
    fetcher: Fetcher,
    ollama_client: httpx.AsyncClient,
) -> dict:
    cfg = opts.cfg
    mode = opts.mode
    question = opts.question
    model = opts.model
    # 1. Validate.
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        return _error(url, ErrorCode.INVALID_URL, f"unsupported scheme: {parts.scheme!r}")

    # 2. Cache lookup.
    key = cache_mod.cache_key(url, mode, question, model)
    if not opts.no_cache:
        hit = cache_mod.read(key, cfg.cache.ttl_hours, CACHE_DIR)
        if hit is not None:
            return hit

    # 3. Robots.
    if not robots.is_allowed(url, opts.ua):
        return _error(
            url,
            ErrorCode.ROBOTS_DISALLOWED,
            f"Robots.txt disallows User-Agent '{opts.ua}' for path '{parts.path or '/'}'.",
            details={"robots_url": f"{parts.scheme}://{parts.netloc}/robots.txt"},
        )

    # 4. Fetch.
    try:
        fetched = await fetcher.fetch(
            url,
            timeout_s=opts.fetch_timeout,
            navigation_wait=cfg.fetch.navigation_wait,
        )
    except FetchFailedError as e:
        return _error(url, ErrorCode.FETCH_FAILED, str(e))

    # 5. Extract.
    extract_start = time.monotonic()
    try:
        markdown, extractor_name = extract_html(fetched.html)
    except ExtractEmptyError as e:
        return _error(url, ErrorCode.EXTRACT_EMPTY, str(e))
    extract_ms = int((time.monotonic() - extract_start) * 1000)

    if opts.verbose:
        click.echo(
            f"  fetch {fetched.elapsed_ms}ms, extract {extract_ms}ms "
            f"(via {extractor_name})",
            err=True,
        )

    # 6. Distill (only when mode includes summary).
    summary = None
    ollama_ms: int | None = None
    truncated = False
    if mode in ("summary", "both"):
        try:
            result, ollama_ms, truncated = await distill(
                ollama_client,
                cfg.ollama.host,
                model,
                question or "",
                markdown,
                cfg.ollama.request_timeout_s,
            )
        except OllamaUnavailableError as e:
            return _error(url, ErrorCode.OLLAMA_UNAVAILABLE, str(e))
        except OllamaTimeoutError as e:
            return _error(url, ErrorCode.OLLAMA_TIMEOUT, str(e))
        except OllamaBadJsonError as e:
            return _error(url, ErrorCode.OLLAMA_BAD_JSON, str(e))
        summary = result.model_dump()
        if opts.verbose:
            click.echo(f"  ollama {ollama_ms}ms", err=True)

    # 7. Compose result.
    extract_block: dict = {
        "extractor": extractor_name,
        "char_count": len(markdown),
    }
    if mode in ("extract", "both"):
        extract_block["markdown"] = markdown

    meta: dict = {
        "playwright_ms": fetched.elapsed_ms,
        "extract_ms": extract_ms,
        "cached": False,
        "robots_checked": True,
        "model": model,
    }
    if ollama_ms is not None:
        meta["ollama_ms"] = ollama_ms
    if truncated:
        meta["truncated"] = True

    result_payload: dict = {
        "url": url,
        "final_url": fetched.final_url,
        "title": fetched.title,
        "fetched_at": _now_iso(),
        "mode": mode,
        "extract": extract_block,
        "meta": meta,
    }
    if summary is not None:
        result_payload["summary"] = summary

    # 8. Cache write.
    if not opts.no_cache:
        cache_mod.write(key, result_payload, CACHE_DIR)

    return result_payload


def _error(
    url: str, code: ErrorCode, message: str, details: dict | None = None
) -> dict:
    payload: dict = {"url": url, "error": message, "code": code.value}
    if details:
        payload["details"] = details
    return payload


def _now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


if __name__ == "__main__":
    main()
