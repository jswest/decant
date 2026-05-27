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
    "model": "qwen3:32b"
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

URLs are fetched sequentially (no parallelism — see [polite scraping](#polite-scraping-policy)). The output is a JSON array in the same order as the arguments. Exit code is non-zero if **any** URL errored.

### Flags

| Flag | Description |
| --- | --- |
| `--question "..."` | Research question. Required for `--mode summary` or `--mode both`. |
| `--mode extract\|summary\|both` | Defaults to `extract` (no question) or `summary` (with question). |
| `--model MODEL` | Override the configured Ollama model for this run. |
| `--no-cache` | Bypass cache for both read and write. |
| `--timeout SECONDS` | Per-URL fetch timeout (default from `fetch.request_timeout_s`). |
| `--verbose` | Emit per-stage timings on stderr. |

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

MIT.
