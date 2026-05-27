# decant

> Clarifying the web for CLI agents.

`decant` is a polite, local-first command-line tool that turns a URL into clean markdown and, optionally, a structured set of findings produced by a local LLM via Ollama.

It exists to save context tokens for hosted coding agents (Claude Code, Cursor, etc.) that would otherwise burn 10k–50k tokens fetching a full web page. The heavy work — browser rendering, content extraction, summarization — stays on your own machine.

## Install

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/), plus a running Ollama instance for summary mode.

```bash
git clone https://github.com/<you>/decant && cd decant
uv sync
uv run playwright install chromium      # one-time browser install
```

Then run the interactive first-run setup:

```bash
uv run decant config
```

This writes `~/.decant/config.yaml` and creates `~/.decant/cache/`.

## Install the skill

The agent-facing skill lives at [`skills/decant.md`](./skills/decant.md). Copy it into your harness's skills directory:

```bash
cp skills/decant.md ~/.claude/skills/decant.md
```

Works with any harness that reads Anthropic's Agent Skills format — Claude Code, [Pi](https://pi.dev), [Goose](https://goose-docs.ai/), Cowork. The skill teaches the agent when to reach for `decant` instead of a built-in fetch, how to invoke each mode (`extract` / `summary` / `both`), how to parse the JSON output, and how to recover from each error code.

For the agent to actually be able to run `decant`, install it as a CLI tool so the binary is on `PATH` from wherever the agent happens to be working:

```bash
uv tool install .                  # or: uv tool install --editable .
```

`uv run decant` only resolves inside this project directory; `uv tool install` makes `decant` available globally.

**Re-copy after every `git pull`.** Pre-1.0, the skill contract changes often — flags rename, output shapes shift, new modes land. Your harness reads the skill from where you copied it, not from this repo. After every pull, overwrite the installed copy:

```bash
cp skills/decant.md ~/.claude/skills/decant.md
```

The CLI itself resolves through the installed package, so the same `uv tool install .` (or `--editable`) keeps `decant <subcommand>` in sync with the contract the skill describes.

## Usage

### Extract clean markdown

```bash
uv run decant url https://example.com/article
```

Output is a single JSON object on stdout:

```json
{
  "url": "https://example.com/article",
  "final_url": "https://example.com/article",
  "title": "Article title",
  "fetched_at": "2026-05-26T12:34:56Z",
  "mode": "extract",
  "extract": {
    "extractor": "trafilatura",
    "char_count": 8214,
    "markdown": "# Article title\n\n..."
  },
  "meta": {
    "playwright_ms": 1234,
    "extract_ms": 56,
    "cached": false,
    "robots_checked": true,
    "model": "qwen3:32b",
    "soft_404": {
      "verdict": "unlikely",
      "reasons": []
    }
  }
}
```

### Ask a research question (summary mode)

```bash
uv run decant url https://example.com/article --question "What is the author's main claim?"
```

Returns Ollama-distilled findings instead of raw markdown:

```json
{
  "...": "...",
  "mode": "summary",
  "summary": {
    "answer": "Direct answer in 1-3 sentences, or null.",
    "page_topic": "What this page is about, one sentence.",
    "findings": [
      {
        "quote": "Verbatim text from the page.",
        "context": "Section heading or surrounding context.",
        "relevance": "high"
      }
    ]
  }
}
```

### Both at once

```bash
uv run decant url https://example.com/article --question "..." --mode both
```

Returns the markdown *and* the summary in one response.

### Multiple URLs

```bash
uv run decant url https://a.com https://b.com https://c.com
```

URLs are fetched sequentially (no parallelism — see [polite scraping](#polite-scraping-policy)). The output is a JSON array in the same order as the arguments. In batch mode a summary line is printed to stderr (`2 succeeded, 1 failed (run completed in 3.4s)`), and the exit code follows this table:

| Scenario | Default | With `--allow-partial` |
| --- | --- | --- |
| All URLs succeeded | 0 | 0 |
| Some succeeded, some failed | 1 | 0 |
| All URLs failed | 1 | 1 |

Default fail-strict is safer for automation. Use `--allow-partial` when one bad URL shouldn't kill downstream shell-pipeline work.

### Search the web (Brave LLM Context API)

```bash
uv run decant search "qwen3 license terms"
```

Hits Brave's LLM Context API and returns a JSON payload of URLs + pre-extracted snippets — input for the orchestrator, not an end product. The intended flow is two steps:

```bash
# 1. Find candidate URLs
uv run decant search "qwen3 license terms"

# 2. Pick the ones worth reading and pipe into decant url
uv run decant url https://huggingface.co/Qwen/Qwen3-32B https://qwenlm.github.io/blog \
  --question "What are the license terms for Qwen3?"
```

The output schema:

```json
{
  "query": "qwen3 license terms",
  "fetched_at": "2026-05-26T12:34:56Z",
  "results": [
    {
      "url": "https://...",
      "title": "...",
      "hostname": "example.com",
      "age": "2025-11-03",
      "snippets": ["...", "..."]
    }
  ],
  "meta": {
    "brave_ms": 412,
    "cached": false,
    "token_budget": 4096,
    "result_count": 10
  }
}
```

Requires a Brave Search API key (set via `decant config` or the `BRAVE_SEARCH_API_KEY` env var). Without one, the command errors with `search_no_api_key`.

Search-specific flags:

| Flag | Description |
| --- | --- |
| `--top N` | Max URLs (Brave `maximum_number_of_urls`). Default 10. |
| `--token-budget N` | Brave `maximum_number_of_tokens` ceiling on the whole response. Default 4096 — sized for triage, not for skipping the second step. |
| `--freshness pd\|pw\|pm\|py\|<range>` | Passthrough to Brave. |
| `--country XX`, `--lang xx` | Per-call overrides for the configured defaults. |
| `--no-cache` | Bypass cache for both read and write. |

Search results have their own cache TTL (default 1 hour, separate from page-extract TTL — search results stale faster). The same `decant cache stats|clear` commands cover both keyspaces.

### Flags

| Flag | Description |
| --- | --- |
| `--question "..."` | Research question. Required for `--mode summary` or `--mode both`. |
| `--mode extract\|summary\|both` | Defaults to `extract` (no question) or `summary` (with question). |
| `--model MODEL` | Override the configured Ollama model for this run. |
| `--no-cache` | Bypass cache for both read and write. |
| `--timeout SECONDS` | Per-URL fetch timeout (default from `fetch.request_timeout_s`). |
| `--allow-partial` | In batch mode, exit 0 if at least one URL succeeded. No effect on single-URL runs. |
| `--verbose` | Emit per-stage timings on stderr. |

### Soft-404 detection

Every successful response carries a `meta.soft_404` verdict so callers can spot pages that returned HTTP 200 but didn't actually serve the requested resource — e.g. an SPA rendering the landing page at an unmatched route. Three signals: a 404-ish title, a `<link rel=canonical>`/`<meta og:url>` whose host+path disagrees with `final_url`, or an extracted body shorter than 200 chars.

```json
"soft_404": { "verdict": "likely", "reasons": ["canonical_mismatch"] }
```

- `likely` — any strong signal (title or canonical) tripped. Treat the result with suspicion.
- `possible` — only the soft signal (short extract) tripped. Worth double-checking.
- `unlikely` — no signals; `reasons` is empty.

Decant does **not** auto-error on `likely` — the verdict is advisory. Calling agents decide what to do (warn the user, try a different URL, etc.).

### Cache

```bash
uv run decant cache stats     # {"entries": N, "total_bytes": M, "oldest": ..., "newest": ...}
uv run decant cache clear     # {"cleared": N, "freed_bytes": M}
```

The cache key is `sha256(url + "|" + mode + "|" + (question or "") + "|" + model)`. Different questions or different models get separate entries. TTL is configurable; default is 24 hours, enforced via file mtime.

### Version

```bash
uv run decant version
```

Emits `{"version": "...", "config_path": "...", "cache_dir": "..."}`.

## Configuration

`~/.decant/config.yaml`:

```yaml
contact_email: john@example.com         # required, embedded in User-Agent

ollama:
  host: http://localhost:11434
  model: qwen3:32b
  request_timeout_s: 300

cache:
  ttl_hours: 24

fetch:
  request_timeout_s: 60
  navigation_wait: networkidle           # or "domcontentloaded"
  block_resources:
    - image
    - font
    - media

search:
  brave_api_key: brv-...                 # optional; BRAVE_SEARCH_API_KEY env var overrides
  ttl_hours: 1                           # search results stale faster than page extracts
  default_top: 10
  default_token_budget: 4096
  default_country: us
  default_lang: en
```

The User-Agent is not configurable. It is built from `contact_email` and the package version: `Decant/<version> (+mailto:<contact_email>)`.

## Pipeline

For each URL:

1. **Validate** the URL parses and uses an `http`/`https` scheme.
2. **Cache lookup** (unless `--no-cache`). A hit short-circuits the rest.
3. **robots.txt** — fetched once per host per run with our User-Agent. Parsed with [`protego`](https://github.com/scrapy/protego). Disallow is fatal for that URL.
4. **Fetch** via headless Chromium (Playwright). Image/font/media requests are aborted at the route handler. One browser context is reused across all URLs in a batch.
5. **Extract** with a three-tier fallback chain. First non-empty result wins:
   1. `trafilatura` → markdown
   2. `readability-lxml` → HTML → `markdownify`
   3. BeautifulSoup (`main` → `article` → `[role=main]` → `body`, with script/style/nav/footer/aside stripped) → `html2text`
6. **Distill** (only for `summary` or `both`). Markdown is sent to Ollama's `/api/chat` with the result schema passed as the `format` parameter so generation is constrained at decode time. The response is validated against the same Pydantic model. One retry on validation failure.
7. **Compose** the result JSON per the schema above.
8. **Cache write** (unless `--no-cache`).

Markdown longer than 80 000 characters is truncated before being sent to Ollama (`meta.truncated: true` flags this).

## Error codes

Errors print as JSON on stdout with a non-zero exit code:

```json
{
  "url": "https://example.com/article",
  "error": "Robots.txt disallows User-Agent 'Decant/0.1 (+mailto:...)' for path '/article'.",
  "code": "robots_disallowed",
  "details": { "robots_url": "https://example.com/robots.txt" }
}
```

The closed set of codes:

| Code | Meaning |
| --- | --- |
| `invalid_url` | URL did not parse or has an unsupported scheme. |
| `robots_disallowed` | robots.txt forbids our UA on this path. |
| `fetch_failed` | Playwright threw (timeout, DNS, refused, 4xx/5xx). |
| `extract_empty` | All three extractors returned empty. |
| `ollama_unavailable` | Could not reach the configured Ollama host. |
| `ollama_timeout` | `/api/chat` exceeded `ollama.request_timeout_s`. |
| `ollama_bad_json` | Model returned non-JSON or off-schema JSON after one retry. |
| `config_missing` | No `~/.decant/config.yaml` — run `decant config`. |
| `search_no_api_key` | `decant search` ran without a Brave key configured. |
| `search_unauthorized` | Brave returned 401/403. Check the API key. |
| `search_rate_limited` | Brave returned 429. `details.retry_after` carries seconds if Brave provided it. |
| `search_unavailable` | Network failure, timeout, 5xx, or other Brave error. |
| `search_bad_query` | Query failed length/word-count validation, or Brave returned 400/422. |

Progress messages (e.g. `Fetching https://...` under `--verbose`) go to stderr.

## Polite scraping policy

Non-negotiable, hardcoded:

- **User-Agent** identifies itself honestly and includes a contact email: `Decant/<version> (+mailto:<contact_email>)`. `decant url` refuses to run without one set.
- **robots.txt** is checked once per host per run. A disallow for our UA is fatal — there is **no** `--ignore-robots` flag. Callers who need to bypass should use their own browser.
- **Sequential fetches.** One URL at a time. No concurrency.
- **No retries on 4xx** other than 429. A single retry on 5xx and 429 with 2-second backoff.
- **No cookies persisted across runs.** Each invocation gets a fresh browser context.

## Non-goals

- **Search.** Decant does not search the web. Callers provide URLs.
- **Crawling.** Decant fetches the URLs it is given. It does not follow links beyond ordinary HTTP redirects.
- **Backwards compatibility with itself.** Pre-1.0: breaking changes to config/cache/output are allowed. Bump the version and clear `~/.decant/cache/`.
- **JS execution sandboxing tricks.** Playwright is always on.

## Development

```bash
uv sync --extra dev
uv run pytest -q                              # ~70 tests, ~0.3s
DECANT_E2E=1 uv run pytest tests/test_fetch.py # adds the real-browser test
```

The implementation lives under `src/decant/`. Module roles are documented in [SPEC.md](./SPEC.md), which is the design-of-record and slightly more detailed than this README.

## License

MIT — see [LICENSE](./LICENSE).
