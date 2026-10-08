from __future__ import annotations

import asyncio
import copy
import json
import platform
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urlsplit

import click
import httpx
from playwright.sync_api import Error as PlaywrightError, sync_playwright

from decant import __version__, cache as cache_mod
from decant.config import (
    CACHE_DIR,
    CONFIG_PATH,
    EMAIL_RE,
    Config,
    ConfigMissingError,
    OllamaConfig,
    load_config,
    save_config,
)
from decant.distill import (
    OllamaBadJsonError,
    OllamaModelMissingError,
    OllamaTimeoutError,
    OllamaUnavailableError,
    distill,
)
from decant.extract import ExtractEmptyError, extract as extract_html
from decant.fetch import BrowserMissingError, FetchFailedError, Fetcher
from decant.models import ErrorCode
from decant.robots import RobotsCache
from decant.search import (
    SearchBadQueryError,
    SearchRateLimitedError,
    SearchUnauthorizedError,
    SearchUnavailableError,
    search,
)
from decant.soft_404 import detect as detect_soft_404
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
@click.option("--email", default=None, help="Contact email (embedded in the User-Agent).")
@click.option("--brave-key", default=None, help="Brave Search API key. Pass '' to clear.")
@click.option("--ollama-host", default=None, help="Ollama host URL.")
@click.option("--model", default=None, help="Accurate-tier Ollama model.")
@click.option("--fast-model", default=None, help="Fast-tier Ollama model. Pass '' to clear.")
@click.option(
    "--ollama/--no-ollama",
    "ollama_enabled",
    default=None,
    help="Turn local-LLM summaries on or off.",
)
@click.option("--ttl-hours", type=int, default=None, help="Page cache TTL in hours.")
def config_cmd(
    email: str | None,
    brave_key: str | None,
    ollama_host: str | None,
    model: str | None,
    fast_model: str | None,
    ollama_enabled: bool | None,
    ttl_hours: int | None,
) -> None:
    """Write ~/.decant/config.yaml. Prompts interactively unless flags are passed."""
    try:
        existing = load_config()
    except ConfigMissingError:
        existing = None

    flags = {
        "email": email,
        "brave_key": brave_key,
        "ollama_host": ollama_host,
        "model": model,
        "fast_model": fast_model,
        "ollama_enabled": ollama_enabled,
        "ttl_hours": ttl_hours,
    }
    if any(v is not None for v in flags.values()):
        _config_from_flags(existing, **flags)
        return

    email = _prompt_email(existing.contact_email if existing else None)
    host = click.prompt(
        "Ollama host",
        default=existing.ollama.host if existing else "http://localhost:11434",
    )
    available_models = _list_ollama_models(host)
    if available_models:
        click.echo(f"Available models on {host}: {', '.join(available_models)}")
    else:
        click.echo(f"(Could not reach {host}/api/tags; prompting blindly.)", err=True)
    model = _prompt_model(
        available_models, existing.ollama.model if existing else None
    )
    fast_model = _prompt_fast_model(
        existing.ollama.fast_model if existing else None
    )
    ttl = click.prompt(
        "Cache TTL hours",
        default=existing.cache.ttl_hours if existing else 24,
        type=int,
    )
    existing_brave_key = existing.search.brave_api_key if existing else None
    brave_prompt = (
        "Brave Search API key (press Enter to keep saved value)"
        if existing_brave_key
        else "Brave Search API key (optional, leave blank to skip)"
    )
    brave_key = click.prompt(
        brave_prompt,
        default=existing_brave_key or "",
        show_default=False,
        hide_input=True,
    ).strip()

    cfg = Config.model_validate(
        {
            "contact_email": email,
            "ollama": {"host": host, "model": model, "fast_model": fast_model},
            "cache": {"ttl_hours": ttl},
            "search": {"brave_api_key": brave_key or None},
        }
    )
    save_config(cfg)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    click.echo(f"Wrote {CONFIG_PATH}")
    if fast_model:
        click.echo(
            f"Note: `decant url --question ...` will default to the fast tier "
            f"({fast_model}). Pass --accurate to opt into {model}."
        )


def _config_from_flags(
    existing: Config | None,
    *,
    email: str | None,
    brave_key: str | None,
    ollama_host: str | None,
    model: str | None,
    fast_model: str | None,
    ollama_enabled: bool | None,
    ttl_hours: int | None,
) -> None:
    """Non-interactive config: overlay the passed flags on the existing config."""
    data = existing.model_dump() if existing else {}
    if email is None and existing is None:
        _emit_config_missing_and_exit(
            "No config yet, so --email is required on the first `decant config` run."
        )
    if email is not None:
        if not EMAIL_RE.match(email):
            click.echo(
                json.dumps(
                    {
                        "error": f"Not a plausible email: {email!r}",
                        "code": ErrorCode.CONFIG_INVALID.value,
                    }
                )
            )
            sys.exit(1)
        data["contact_email"] = email
    ollama = data.setdefault("ollama", {})
    if ollama_host is not None:
        ollama["host"] = ollama_host
    if model is not None:
        ollama["model"] = model
    if fast_model is not None:
        ollama["fast_model"] = fast_model or None
    if ollama_enabled is not None:
        ollama["enabled"] = ollama_enabled
    if ttl_hours is not None:
        data.setdefault("cache", {})["ttl_hours"] = ttl_hours
    if brave_key is not None:
        data.setdefault("search", {})["brave_api_key"] = brave_key or None

    cfg = Config.model_validate(data)
    save_config(cfg)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    click.echo(
        json.dumps(
            {
                "config_path": str(CONFIG_PATH),
                "contact_email": cfg.contact_email,
                "ollama": {
                    "enabled": cfg.ollama.enabled,
                    "host": cfg.ollama.host,
                    "model": cfg.ollama.model,
                    "fast_model": cfg.ollama.fast_model,
                },
                "brave_key": bool(cfg.search.brave_api_key),
            }
        )
    )


def _prompt_email(default: str | None) -> str:
    while True:
        email = click.prompt("Contact email", default=default)
        if EMAIL_RE.match(email):
            return email
        click.echo("Not a plausible email; try again.", err=True)


def _prompt_model(available_models: list[str], default: str | None) -> str:
    if available_models and default not in available_models:
        default = available_models[0]
    return click.prompt("Ollama model", default=default)


def _prompt_fast_model(default: str | None) -> str | None:
    raw = click.prompt(
        "Fast Ollama model (optional, leave blank to skip)",
        default=default or "",
        show_default=bool(default),
    ).strip()
    return raw or None


def _list_ollama_models(host: str) -> list[str]:
    try:
        r = httpx.get(f"{host.rstrip('/')}/api/tags", timeout=5)
        r.raise_for_status()
        return [m["name"] for m in r.json().get("models", [])]
    except (httpx.HTTPError, ValueError, KeyError):
        return []


# ---------------------------------------------------------------------------
# decant setup
# ---------------------------------------------------------------------------


@main.command("setup")
def setup_cmd() -> None:
    """Install headless Chromium, then print a JSON status report."""
    subprocess.run(
        [sys.executable, "-m", "playwright", "install", "chromium"],
        check=True,
        stdout=sys.stderr,
    )
    browser_error = _check_browser()

    try:
        cfg = load_config()
    except ConfigMissingError:
        cfg = None
    ollama_cfg = cfg.ollama if cfg else OllamaConfig()
    models = _list_ollama_models(ollama_cfg.host)

    browser: dict = {"ok": browser_error is None}
    if browser_error:
        browser["error"] = browser_error
        if platform.system() == "Linux":
            browser["fix"] = (
                f"sudo {sys.executable} -m playwright install-deps chromium"
            )
    click.echo(
        json.dumps(
            {
                "version": __version__,
                "platform": platform.system(),
                "browser": browser,
                "config": "ok" if cfg else "missing",
                "config_path": str(CONFIG_PATH),
                "contact_email": cfg.contact_email if cfg else None,
                "ollama": {
                    "enabled": ollama_cfg.enabled,
                    "host": ollama_cfg.host,
                    "reachable": bool(models),
                    "models": models,
                    "model": ollama_cfg.model,
                    "fast_model": ollama_cfg.fast_model,
                },
                "brave_key": bool(cfg and cfg.search.brave_api_key),
            },
            indent=2,
        )
    )
    if browser_error:
        sys.exit(1)


def _check_browser() -> str | None:
    """Launch and close headless Chromium; return the error message on failure."""
    try:
        with sync_playwright() as pw:
            pw.chromium.launch(headless=True).close()
    except PlaywrightError as e:
        return str(e)
    return None


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
# decant search — Brave LLM Context API
# ---------------------------------------------------------------------------


@main.command("search")
@click.argument("body")
@click.option("--top", type=int, default=None, help="Max URLs (default from config).")
@click.option("--freshness", default=None, help="Brave freshness filter (pd|pw|pm|py|range).")
@click.option("--country", default=None, help="Override default_country for this run.")
@click.option("--lang", default=None, help="Override default_lang for this run.")
@click.option(
    "--token-budget",
    "token_budget",
    type=int,
    default=None,
    help="Brave maximum_number_of_tokens (default from config).",
)
@click.option("--no-cache", is_flag=True, help="Bypass cache read and write.")
def search_cmd(
    body: str,
    top: int | None,
    freshness: str | None,
    country: str | None,
    lang: str | None,
    token_budget: int | None,
    no_cache: bool,
) -> None:
    """Hit Brave's LLM Context API; emit JSON whose URLs feed into `decant url`."""
    try:
        cfg = load_config()
    except ConfigMissingError:
        _emit_config_missing_and_exit()

    api_key = cfg.search.brave_api_key
    if not api_key:
        click.echo(
            json.dumps(
                {
                    "query": body,
                    "error": (
                        "No Brave API key. Set it with `decant config --brave-key KEY` "
                        "or BRAVE_SEARCH_API_KEY, or find URLs another way and "
                        "read them with `decant url`."
                    ),
                    "code": ErrorCode.SEARCH_NO_API_KEY.value,
                }
            )
        )
        sys.exit(1)

    top = top or cfg.search.default_top
    token_budget = token_budget or cfg.search.default_token_budget
    country = country or cfg.search.default_country
    lang = lang or cfg.search.default_lang

    key = cache_mod.search_cache_key(body, top, freshness, country, lang, token_budget)
    if not no_cache:
        hit = cache_mod.read(key, cfg.search.ttl_hours, CACHE_DIR)
        if hit is not None:
            click.echo(json.dumps(hit, indent=2))
            return

    payload = asyncio.run(
        _run_search(api_key, body, top, token_budget, freshness, country, lang)
    )
    if "error" in payload:
        click.echo(json.dumps(payload, indent=2))
        sys.exit(1)

    if not no_cache:
        cache_mod.write(key, payload, CACHE_DIR)
    click.echo(json.dumps(payload, indent=2))


async def _run_search(
    api_key: str,
    body: str,
    top: int,
    token_budget: int,
    freshness: str | None,
    country: str,
    lang: str,
) -> dict:
    async with httpx.AsyncClient() as client:
        try:
            flattened, brave_ms = await search(
                client,
                api_key,
                body,
                top=top,
                token_budget=token_budget,
                freshness=freshness,
                country=country,
                lang=lang,
            )
        except SearchBadQueryError as e:
            return _search_error(body, ErrorCode.SEARCH_BAD_QUERY, str(e))
        except SearchUnauthorizedError as e:
            return _search_error(body, ErrorCode.SEARCH_UNAUTHORIZED, str(e))
        except SearchRateLimitedError as e:
            details = {"retry_after": e.retry_after} if e.retry_after else None
            return _search_error(body, ErrorCode.SEARCH_RATE_LIMITED, str(e), details)
        except SearchUnavailableError as e:
            return _search_error(body, ErrorCode.SEARCH_UNAVAILABLE, str(e))

    return {
        "query": body,
        "fetched_at": _now_iso(),
        "results": flattened["results"],
        "meta": {
            "brave_ms": brave_ms,
            "cached": False,
            "token_budget": token_budget,
            "result_count": len(flattened["results"]),
        },
    }


def _search_error(
    query: str, code: ErrorCode, message: str, details: dict | None = None
) -> dict:
    payload: dict = {"query": query, "error": message, "code": code.value}
    if details:
        payload["details"] = details
    return payload


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
@click.option(
    "--fast",
    is_flag=True,
    help=(
        "Use ollama.fast_model. Already the default when fast_model is configured; "
        "useful for scripts that want to be explicit. Ignored if --model is set."
    ),
)
@click.option(
    "--accurate",
    is_flag=True,
    help=(
        "Use ollama.model (the accurate tier). Opt-in for the cases where the "
        "extra latency pays off. Ignored if --model is set."
    ),
)
@click.option(
    "--terse",
    is_flag=True,
    help="Drop verbatim source quotes from summary output. Smaller, less auditable.",
)
@click.option("--no-cache", is_flag=True, help="Bypass cache read and write.")
@click.option(
    "--timeout",
    "timeout_override",
    type=int,
    default=None,
    help="Per-URL fetch timeout in seconds (defaults to fetch.request_timeout_s).",
)
@click.option(
    "--allow-partial",
    is_flag=True,
    help="In batch mode, exit 0 if at least one URL succeeded (default: fail-strict).",
)
@click.pass_context
def url_cmd(
    ctx: click.Context,
    urls: tuple[str, ...],
    question: str | None,
    mode: str | None,
    model: str | None,
    fast: bool,
    accurate: bool,
    terse: bool,
    no_cache: bool,
    timeout_override: int | None,
    allow_partial: bool,
) -> None:
    """Fetch one or more URLs sequentially and emit JSON."""
    if fast and accurate:
        raise click.UsageError("--fast and --accurate are mutually exclusive.")

    try:
        cfg = load_config()
    except ConfigMissingError:
        _emit_config_missing_and_exit()

    resolved_model, tier = _resolve_model(cfg, model, fast, accurate)
    if resolved_model is None:
        _emit_config_missing_fast_model_and_exit()

    resolved_mode = _resolve_mode(mode, question)
    if terse and resolved_mode == "extract":
        raise click.UsageError("--terse requires --question or --mode summary|both")
    summary_skipped = None
    if resolved_mode != "extract" and not cfg.ollama.enabled:
        resolved_mode, terse, summary_skipped = "extract", False, "ollama_disabled"

    opts = _RunOpts(
        cfg=cfg,
        mode=resolved_mode,
        question=question,
        model=resolved_model,
        tier=tier,
        terse=terse,
        fetch_timeout=timeout_override or cfg.fetch.request_timeout_s,
        no_cache=no_cache,
        verbose=ctx.obj.get("verbose", False),
        ua=build_user_agent(cfg.contact_email),
        summary_skipped=summary_skipped,
    )

    is_batch = len(urls) > 1
    batch_start = time.monotonic()
    try:
        results = asyncio.run(_process_all(opts, urls))
    except BrowserMissingError as e:
        results = [
            _error(
                u,
                ErrorCode.BROWSER_MISSING,
                f"Chromium failed to launch. Run `decant setup` to install it. ({e})",
            )
            for u in urls
        ]
    elapsed_s = time.monotonic() - batch_start

    payload = results if is_batch else results[0]
    click.echo(json.dumps(payload, indent=2))

    succeeded = sum(1 for r in results if "error" not in r)
    failed = len(results) - succeeded

    if is_batch:
        click.echo(
            f"{succeeded} succeeded, {failed} failed "
            f"(run completed in {elapsed_s:.1f}s)",
            err=True,
        )

    if failed > 0 and (succeeded == 0 or not allow_partial):
        sys.exit(1)


def _resolve_mode(mode: str | None, question: str | None) -> str:
    if mode is None:
        return "summary" if question else "extract"
    if mode in ("summary", "both") and not question:
        raise click.UsageError(f"--mode {mode} requires --question")
    return mode


def _resolve_model(
    cfg: Config, model_opt: str | None, fast: bool, accurate: bool
) -> tuple[str | None, str]:
    """Pick (model, tier).

    Order:
      1. --model X explicit              → (X, "explicit")
      2. --fast                          → (cfg.ollama.fast_model, "fast")
                                            — caller errors out if that's None
      3. --accurate                      → (cfg.ollama.model, "accurate")
      4. fast_model configured (default) → (cfg.ollama.fast_model, "fast")
      5. otherwise                       → (cfg.ollama.model, "accurate")
    """
    if model_opt:
        return model_opt, "explicit"
    if fast:
        return cfg.ollama.fast_model, "fast"
    if accurate:
        return cfg.ollama.model, "accurate"
    if cfg.ollama.fast_model:
        return cfg.ollama.fast_model, "fast"
    return cfg.ollama.model, "accurate"


_SETUP_HINT = (
    "Run the /decant:setup skill, or `decant config --email you@example.com`."
)


def _emit_config_missing_and_exit(message: str | None = None) -> None:
    click.echo(
        json.dumps(
            {
                "error": message or f"No ~/.decant/config.yaml. {_SETUP_HINT}",
                "code": ErrorCode.CONFIG_MISSING.value,
                "details": {"config_path": str(CONFIG_PATH)},
            }
        )
    )
    sys.exit(1)


def _emit_config_missing_fast_model_and_exit() -> None:
    click.echo(
        json.dumps(
            {
                "error": (
                    "--fast requires ollama.fast_model to be set. Run "
                    "`decant config --fast-model MODEL`, or pass --model X explicitly."
                ),
                "code": ErrorCode.CONFIG_MISSING_FAST_MODEL.value,
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
    tier: str
    terse: bool
    fetch_timeout: int
    no_cache: bool
    verbose: bool
    ua: str
    summary_skipped: str | None = None


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
    key = cache_mod.cache_key(url, mode, question, model, opts.terse)
    if not opts.no_cache:
        hit = cache_mod.read(key, cfg.cache.ttl_hours, CACHE_DIR)
        if hit is not None:
            if opts.summary_skipped:
                hit["meta"]["summary_skipped"] = opts.summary_skipped
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
        markdown, extractor_name = extract_html(fetched.html, fetched.article)
    except ExtractEmptyError as e:
        return _error(url, ErrorCode.EXTRACT_EMPTY, str(e))
    extract_ms = int((time.monotonic() - extract_start) * 1000)

    if opts.verbose:
        click.echo(
            f"  fetch {fetched.elapsed_ms}ms, extract {extract_ms}ms "
            f"(via {extractor_name})",
            err=True,
        )

    extract_block: dict = {
        "extractor": extractor_name,
        "char_count": len(markdown),
    }
    if mode in ("extract", "both"):
        extract_block["markdown"] = markdown

    soft_404_meta = detect_soft_404(
        final_url=fetched.final_url,
        html=fetched.html,
        markdown=markdown,
        title=fetched.title,
    )

    meta: dict = {
        "playwright_ms": fetched.elapsed_ms,
        "extract_ms": extract_ms,
        "cached": False,
        "robots_checked": True,
        "model": model,
        "soft_404": soft_404_meta,
    }

    result_payload: dict = {
        "url": url,
        "final_url": fetched.final_url,
        "title": fetched.title,
        "fetched_at": _now_iso(),
        "mode": mode,
        "extract": extract_block,
        "meta": meta,
    }

    # Warm the extract cache before distilling, so an Ollama failure doesn't
    # throw away the fetch: a follow-up `--mode extract` call is a cache hit.
    # Extract-mode keys ignore question + model (see cache.cache_key).
    if not opts.no_cache and mode != "extract":
        extract_payload = _extract_projection(
            {**result_payload, "extract": {**extract_block, "markdown": markdown}}
        )
        cache_mod.write(
            cache_mod.cache_key(url, "extract", None, ""), extract_payload, CACHE_DIR
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
                terse=opts.terse,
            )
        except OllamaUnavailableError as e:
            return _error(url, ErrorCode.OLLAMA_UNAVAILABLE, str(e))
        except OllamaModelMissingError as e:
            return _error(
                url,
                ErrorCode.OLLAMA_MODEL_MISSING,
                f"{e}. Run `ollama pull {model}`.",
                details={"model": model},
            )
        except OllamaTimeoutError as e:
            return _error(url, ErrorCode.OLLAMA_TIMEOUT, str(e))
        except OllamaBadJsonError as e:
            return _error(url, ErrorCode.OLLAMA_BAD_JSON, str(e))
        summary = result.model_dump()
        if opts.verbose:
            click.echo(f"  ollama {ollama_ms}ms", err=True)

    # 7. Compose result.
    if ollama_ms is not None:
        meta["ollama_ms"] = ollama_ms
    if mode in ("summary", "both"):
        meta["tier"] = opts.tier
        meta["terse"] = opts.terse
    if truncated:
        meta["truncated"] = True
    if summary is not None:
        result_payload["summary"] = summary

    # 8. Cache write. `--mode both` also warms the single-mode caches via
    # projections so a follow-up `--mode extract` or `--mode summary` call
    # against the same URL hits cache instead of re-fetching.
    if not opts.no_cache:
        cache_mod.write(key, result_payload, CACHE_DIR)
        if mode == "both":
            summary_key = cache_mod.cache_key(
                url, "summary", question, model, opts.terse
            )
            cache_mod.write(summary_key, _summary_projection(result_payload), CACHE_DIR)

    # Per-run annotation, added after the cache write so a later plain
    # extract call doesn't inherit it.
    if opts.summary_skipped:
        meta["summary_skipped"] = opts.summary_skipped

    return result_payload


def _extract_projection(payload: dict) -> dict:
    """Return what a fresh `--mode extract` run for the same URL would have written.

    deepcopy is required: `payload` is returned to the caller and json-dumped to
    stdout, so in-place mutation would corrupt the user-facing output.
    """
    p = copy.deepcopy(payload)
    p["mode"] = "extract"
    p.pop("summary", None)
    p["meta"].pop("ollama_ms", None)
    p["meta"].pop("tier", None)
    p["meta"].pop("terse", None)
    p["meta"].pop("truncated", None)
    return p


def _summary_projection(payload: dict) -> dict:
    """Return what a fresh `--mode summary` run for same URL+question+model would have written."""
    p = copy.deepcopy(payload)
    p["mode"] = "summary"
    p["extract"].pop("markdown", None)
    return p


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
