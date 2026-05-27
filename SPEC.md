# Decant — Specification

> Pour the clear liquid off; leave the sediment behind.

## Purpose

`decant` is a polite, local-first command-line tool for turning a URL into clean markdown and (optionally) into a structured set of findings produced by a local LLM. It exists to:

1. Save context tokens for hosted LLM agents (e.g. Claude Code) that would otherwise burn 10k–50k tokens fetching a full web page.
2. Keep the heavy work — browser rendering, content extraction, summarization — on the user's own machine.
3. Be a good web citizen: respects `robots.txt`, identifies itself honestly, fetches sequentially.

The intended caller is a coding agent (or human) who has access to a beefy local Ollama instance and wants distilled answers rather than raw HTML.

## Non-goals

- **Scraping search engines.** Decant does not scrape DuckDuckGo/Google/Bing. It *does* ship a `decant search` subcommand backed by Brave's paid LLM Context API; that's an API call, not a scrape, and only works when the user has provided a Brave key. If Brave is unconfigured the subcommand errors with `search_no_api_key`.
- **Crawling.** Decant fetches the URLs it is given. It does not follow links beyond ordinary HTTP redirects.
- **Backwards compatibility with itself.** Pre-1.0: breaking config/cache/output changes are allowed; bump the version and tell users to clear `~/.decant/cache/`.
- **JS execution sandboxing tricks.** Playwright is always on. We do not try to be clever about static-only fetches.
- **A `--ignore-robots` escape hatch.** If a site disallows our UA, `decant` returns a `robots_disallowed` error. The caller can fall back to their own browser/tool. Decant itself stays polite.

## CLI

The binary is `decant`. Subcommand-style interface (Click).

### `decant config`

Interactive first-run setup. Reads existing `~/.decant/config.yaml` if present and uses its values as defaults.

Prompts:
1. **Contact email** (required) — embedded in the User-Agent string so site operators can reach you. No default. Validated as a plausible email.
2. **Ollama host** (default `http://localhost:11434`).
3. **Ollama model** (default: first model returned by `ollama list`, else prompts blindly). Validated by hitting `/api/tags` on the host.
4. **Cache TTL hours** (default `24`).
5. **Brave Search API key** (optional, skip with blank input). Hidden input. Stored under `search.brave_api_key`. The `BRAVE_SEARCH_API_KEY` env var, if set, overrides the file value at load time.

Writes `~/.decant/config.yaml`. Creates `~/.decant/cache/` if missing. Idempotent — running it again lets you re-edit.

### `decant url <URL> [<URL> ...]`

Primary command. Fetches one or more URLs sequentially and emits JSON.

Flags:
- `--question "..."` — research question. Required for `--mode summary` or `--mode both`. When provided without `--mode`, defaults to `summary`.
- `--mode extract|summary|both` — default `extract` if no question, `summary` if question given.
  - `extract`: returns clean markdown only. No Ollama call.
  - `summary`: returns structured findings from Ollama; omits the raw markdown.
  - `both`: returns markdown **and** Ollama findings.
- `--model MODEL` — override the configured Ollama model for this run.
- `--no-cache` — bypass cache for both read and write on this run.
- `--timeout SECONDS` — per-URL hard ceiling (default 60s for fetch, 300s for Ollama).

Output:
- Single URL → one JSON object on stdout.
- Multiple URLs → a JSON array, in the same order as the arguments, one element per URL. Per-URL errors appear as error objects within the array; exit code is non-zero if **any** URL errored.

### `decant search <body>`

Hits Brave's **LLM Context API** (`/res/v1/llm/context`, launched Feb 2026) and emits JSON whose URLs can be fed back into `decant url` for full extraction.

The output of `decant search` is **input for the orchestrator**, not an end product. The intended caller is a coding agent: it calls `decant search`, picks promising URLs from the snippets, then calls `decant url <those> --question "..."`. There is intentionally no `--distill` mode here — the orchestrator *is* the distill loop.

Flags:
- `--top N` — Brave `maximum_number_of_urls`. Default from `search.default_top`.
- `--freshness pd|pw|pm|py|<range>` — passthrough to Brave.
- `--country XX`, `--lang xx` — per-call overrides for the config defaults.
- `--token-budget N` — Brave `maximum_number_of_tokens` (ceiling on the whole response). Default from `search.default_token_budget` (4096).
- `--no-cache` — bypass cache for both read and write on this run.

Body constraints (validated client-side before any HTTP call): 1–400 chars, ≤50 words. Out-of-range queries error with `search_bad_query` without hitting Brave.

Brave's `maximum_number_of_snippets` and `maximum_number_of_snippets_per_url` are not exposed as flags in v0; the request relies on Brave's defaults.

Output schema:

```json
{
  "query": "qwen3 license",
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

Notes:
- Flattens Brave's `grounding.generic[]` + `sources{}` into one `results[]`. Items whose URL isn't in `sources` are dropped — this is how POI/map results are silently filtered out.
- `age` may be `null`.
- POI / map results are out of scope in v0.

Caching: separate keyspace from URL results. Key is `sha256("search|" + query + "|" + top + "|" + freshness + "|" + country + "|" + lang + "|" + token_budget)`. TTL is `search.ttl_hours` (default `1`, since search results stale faster than page extracts). `decant cache stats` / `decant cache clear` cover search entries automatically.

### `decant cache clear`

Deletes the contents of `~/.decant/cache/` (not the directory itself). Prints `{"cleared": N, "freed_bytes": M}`.

### `decant cache stats`

Prints `{"entries": N, "total_bytes": M, "oldest": iso8601, "newest": iso8601}`.

### `decant version`

Prints `{"version": "...", "config_path": "...", "cache_dir": "..."}`.

## Configuration file

Location: `~/.decant/config.yaml`. Schema:

```yaml
contact_email: john@example.com         # required, embedded in User-Agent

ollama:
  host: http://localhost:11434
  model: qwen3:32b                       # name as it appears in `ollama list`
  request_timeout_s: 300                 # ceiling for a single /api/chat call

cache:
  ttl_hours: 24
  # cache directory is fixed at ~/.decant/cache/

fetch:
  request_timeout_s: 60                  # hard ceiling for one URL's Playwright cycle
  navigation_wait: networkidle           # or "domcontentloaded"
  block_resources:                       # passed to Playwright route handler
    - image
    - font
    - media

search:
  brave_api_key: brv-...                 # optional; BRAVE_SEARCH_API_KEY env var overrides
  ttl_hours: 1                           # separate from page-cache TTL
  default_top: 10
  default_token_budget: 4096
  default_country: us
  default_lang: en

# user_agent is not configurable — it is built from contact_email and version:
#   Decant/<version> (+mailto:<contact_email>)
```

Loading: missing keys fall back to defaults; missing file is an error for any command other than `config` and `version`.

## Pipeline

For each URL, in order:

1. **Validate** — URL must parse, scheme must be `http` or `https`. Otherwise: `invalid_url`.
2. **Cache lookup** (unless `--no-cache`) — compute key as `sha256(url + "|" + mode + "|" + (question or "") + "|" + model)`. If `~/.decant/cache/<key>.json` exists and `mtime` is within `cache.ttl_hours`, return cached result with `meta.cached: true`.
3. **robots.txt** — fetch `<scheme>://<host>/robots.txt` (with our UA). Parse with `protego` (more spec-correct than stdlib `urllib.robotparser`). Cache parsed result in-process for the duration of the run. If our UA is disallowed for the path: emit `robots_disallowed` error and stop.
4. **Playwright fetch** — headless Chromium, block image/font/media resources via route handler, wait for `domcontentloaded` then `networkidle` (cap at `fetch.request_timeout_s`). Set User-Agent. Capture `final_url`, page title, full HTML.
5. **Extract** — three-tier fallback chain. Stop at first non-empty result:
    1. `trafilatura.extract(html, output_format="markdown", include_links=True)`
    2. `readability.Document(html).summary()` → HTML of main article → convert to markdown via `markdownify`
    3. BeautifulSoup: select `main, article, [role=main]` (in that order, first hit wins); if none, use `body`. Strip `<script>`, `<style>`, `<nav>`, `<footer>`, `<aside>`. Pipe through `html2text`.
    
    Record which extractor produced the result in `meta.extractor`.
6. **Distill** (only if mode includes summary) — POST to `<ollama.host>/api/chat`. Single user turn with the prompt below. Pass the Pydantic-derived JSON Schema as Ollama's `format` parameter so generation is constrained to the result shape at decode time. Validate the response with the same Pydantic model before returning.
7. **Compose result JSON** per the output schema.
8. **Cache write** (unless `--no-cache`) — write the result JSON to `~/.decant/cache/<key>.json`. Set `meta.cached: false` in the returned copy.

URLs are processed strictly sequentially. No `--parallel` flag in v0.

## Caching

- **Location:** `~/.decant/cache/` (one JSON file per entry).
- **Key:** `sha256(url + "|" + mode + "|" + (question or "") + "|" + model)`. Different questions against the same URL get separate cache entries. Different models likewise — switching from `qwen3:32b` to `qwen3:70b` won't reuse the prior distillation.
- **TTL:** `cache.ttl_hours` from config, default 24. Enforced via file `mtime`. No background cleanup; stale files are simply overwritten on next miss.
- **Eviction:** none in v0. `decant cache clear` is the only sweep.
- **Cache invariant:** a cached result is byte-identical to a fresh result for the same key, except `meta.cached` flips to `true` and `meta.cached_at` is added.

## Polite scraping policy

Non-negotiable, hardcoded:

- **User-Agent:** `Decant/<version> (+mailto:<contact_email>)`. The contact email comes from config; `decant url` refuses to run without one set.
- **Robots:** `robots.txt` is checked for every host before the first fetch to that host in a run. A `disallow` for our UA on the requested path is fatal for that URL.
- **Sequential fetches:** one URL at a time, no concurrency. Provides natural rate-limiting without per-host bookkeeping.
- **No retries on 4xx** (other than 429). A single retry on 5xx with 2s backoff.
- **No cookies persisted across runs.** Each run is a fresh browser context.

## Output schema

### Success

```json
{
  "url": "https://example.com/article",
  "final_url": "https://example.com/article",
  "title": "Article title",
  "fetched_at": "2026-05-26T12:34:56Z",
  "mode": "summary",
  "extract": {
    "extractor": "trafilatura",
    "char_count": 8214,
    "markdown": "# Article title\n\n..."        // omitted when mode == "summary"
  },
  "summary": {                                   // omitted when mode == "extract"
    "answer": "Direct answer in 1-3 sentences, or null.",
    "page_topic": "What this page is about, one sentence.",
    "findings": [
      {
        "quote": "Verbatim text from the page.",
        "context": "Section heading or surrounding context.",
        "relevance": "high"
      }
    ]
  },
  "meta": {
    "playwright_ms": 1234,
    "extract_ms": 56,
    "ollama_ms": 4521,
    "cached": false,
    "robots_checked": true,
    "model": "qwen3:32b"
  }
}
```

Notes:
- `mode == "summary"`: `extract.markdown` is omitted to keep the response compact; `extract.extractor` and `extract.char_count` are still returned so the caller knows what Ollama saw.
- `mode == "both"`: both `extract.markdown` and `summary` are present.
- `mode == "extract"`: `summary` is omitted; `ollama_ms` is absent from `meta`.

### Error

```json
{
  "url": "https://example.com/article",
  "error": "Robots.txt disallows User-Agent 'Decant/0.1 (+mailto:...)' for path '/article'.",
  "code": "robots_disallowed",
  "details": { "robots_url": "https://example.com/robots.txt" }
}
```

Codes (closed set):
- `invalid_url` — URL did not parse or has an unsupported scheme.
- `robots_disallowed` — robots.txt forbids our UA on this path.
- `fetch_failed` — Playwright threw (timeout, DNS, connection refused, 4xx/5xx).
- `extract_empty` — all three extractors returned empty/whitespace.
- `ollama_unavailable` — could not reach the configured Ollama host.
- `ollama_timeout` — `/api/chat` exceeded `ollama.request_timeout_s`.
- `ollama_bad_json` — model returned non-JSON or JSON that did not match the schema, even after one retry.
- `config_missing` — no `~/.decant/config.yaml` (run `decant config` first).
- `search_no_api_key` — `decant search` invoked without a Brave key in config or env.
- `search_unauthorized` — Brave returned 401 or 403.
- `search_rate_limited` — Brave returned 429. `details.retry_after` carries the parsed `Retry-After` header in seconds, if any.
- `search_unavailable` — network failure, timeout, 5xx, or any other unmapped HTTP status from Brave.
- `search_bad_query` — query failed client-side validation (length/word count) or Brave returned 400/422.

Errors print as JSON on stdout, exit code is non-zero. Progress messages (e.g. "Fetching https://..." while a batch runs) go to stderr. Search errors use the same envelope as URL errors but key the subject as `query` instead of `url`.

## Ollama prompt and structured output

Used for `mode in {summary, both}`. Sent as a single user turn.

**Structured output via Pydantic.** The result schema is defined once as a Pydantic model in `decant.models`:

```python
class Finding(BaseModel):
    quote: str = Field(..., max_length=2000)
    context: str
    relevance: Literal["high", "medium", "low"]

class DistillResult(BaseModel):
    answer: str | None
    page_topic: str
    findings: list[Finding] = Field(..., max_length=8)
```

The Pydantic model serves two roles:

1. **Constrain Ollama's decoder.** Pass `DistillResult.model_json_schema()` as the `format` field on the `/api/chat` request body. Ollama (v0.5+) constrains token generation to satisfy the schema — empty/malformed JSON and wrong-shaped objects become essentially impossible at the syntactic level.
2. **Validate on return.** Parse Ollama's response with `DistillResult.model_validate_json(content)`. This catches the rare cases the schema-constrained decoder can't (e.g. an older Ollama that ignores `format`, or a model producing semantically wrong but schema-shaped content if we add stricter field constraints later).

The textual prompt remains useful for steering content quality (verbatim quotes, no invention) even though the schema enforces shape:

```
You are extracting information from a web page to help answer a research question.

QUESTION:
{question}

PAGE CONTENT (markdown):
---
{markdown}
---

Return ONLY a JSON object with this exact schema:

{
  "answer": "<direct answer to the question in 1-3 sentences, or null if the page does not answer it>",
  "page_topic": "<one sentence describing what this page is about>",
  "findings": [
    {
      "quote": "<verbatim text from the page, up to 200 words per quote>",
      "context": "<the section heading or surrounding context>",
      "relevance": "high" | "medium" | "low"
    }
  ]
}

RULES:
- Quotes must be verbatim. Do not paraphrase, do not edit for length other than truncating with an ellipsis if needed.
- Maximum 8 findings.
- If the page does not answer the question, set "answer" to null. Still include up to 3 tangentially relevant findings if any exist.
- Do not invent content not present in the page.
- Output ONLY the JSON object. No commentary, no markdown fences.
```

Truncation: if extracted markdown exceeds `80_000` characters, send only the first 80k and record `meta.truncated: true`. (qwen3 has plenty of context, but we don't want a runaway page to lock the GPU for minutes.) Threshold is **not** configurable in v0.

Retry: if Pydantic validation fails on the response, retry exactly once with the same request. If it fails again, return `ollama_bad_json` with the Pydantic error in `details`. With schema-constrained generation this should be very rare; the retry exists to cover transient model glitches and the edge case where the Ollama server silently ignored `format`.

## Project layout

```
~/Code/decant/
├── SPEC.md
├── README.md                   # user-facing, written after v0 works
├── pyproject.toml              # uv-managed; entry point: decant = "decant.cli:main"
├── src/
│   └── decant/
│       ├── __init__.py
│       ├── cli.py              # Click app, subcommands
│       ├── config.py           # load/save ~/.decant/config.yaml
│       ├── cache.py            # sha256-keyed JSON file cache with TTL
│       ├── robots.py           # protego wrapper, per-run host cache
│       ├── fetch.py            # Playwright orchestration
│       ├── extract.py          # trafilatura → readability → bs4+html2text
│       ├── distill.py          # Ollama /api/chat client + prompt
│       ├── models.py           # pydantic models: DistillResult drives both the
│       │                       # /api/chat `format` schema and response validation
│       └── ua.py               # User-Agent string builder
└── tests/
    ├── test_extract.py         # fixtures: saved HTML snapshots
    ├── test_cache.py
    ├── test_robots.py
    └── test_cli.py             # smoke tests with a local httpbin/Playwright stub
```

## Dependencies

Minimum set, pinned in `pyproject.toml`:

- `click` — CLI
- `pyyaml` — config
- `playwright` — fetch (also requires `playwright install chromium` post-install)
- `trafilatura` — primary extractor
- `readability-lxml` — secondary extractor
- `markdownify` — HTML → MD bridge for the readability path
- `beautifulsoup4` + `html2text` — tertiary extractor
- `protego` — robots.txt parser
- `httpx` — Ollama HTTP client and robots.txt fetch
- `pydantic` — result/error schemas

Python 3.11+. Managed with `uv`.

## Open questions deferred to implementation

- Should the Playwright browser context be reused across URLs in a single batch run? (Probably yes — saves ~1s per URL.) Decide during implementation; either way, document.
- Should `decant version` emit non-JSON when stdout is a TTY for ergonomics? v0: always JSON, keep it boring.
- Logging: stderr progress lines should be quiet by default, with `--verbose` to surface per-stage timings. Confirm during impl.
