# decant

> Clarifying the web for CLI agents.

`decant` is a polite, local-first command-line tool that turns a URL into clean markdown and, optionally, a structured set of findings produced by a local LLM via Ollama.

It exists to save context tokens for hosted coding agents (Claude Code, Cursor, etc.) that would otherwise burn 10k–50k tokens fetching a full web page. The heavy work — browser rendering, content extraction, summarization — stays on your own machine.

## How it compares to WebSearch

The closest alternative for most agents is the built-in `WebSearch` tool. Indexed against WebSearch on a 5-URL corpus (full methodology and per-URL scores in [`docs/REPORT-extraction-results-3.md`](./docs/REPORT-extraction-results-3.md)):

| Retriever | Tokens | Recall | Time |
|---|---:|---:|---:|
| `decant url` (extract mode) | 3717% | 92% | 33% |
| **WebSearch (baseline)** | **100%** | **100%** | **100%** |
| `decant url --question` (fast) | 75% | 93% | 533% |
| `decant url --question --accurate` | 51% | 86% | 617% |
| `decant url --question --terse` | 25% | 75% | 367% |
| `decant url --question --accurate --terse` | 23% | 76% | 467% |

Lower is better for tokens and time; higher is better for recall. WebSearch wall-clock is a subjective ~6s for a parallel batch — treat the time column as order-of-magnitude.

The shape of the tradeoff: decant trades 4–6× more wall-clock for 25–75% of WebSearch's token cost, with recall within ±15 points of WebSearch depending on mode. WebSearch's recall also drifts run-to-run as its index changes (REPORT-2 had it at 73% vs 100% baseline here), so the recall column is the noisiest signal.

## Install

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/), plus a running Ollama instance for summary mode and a [Brave Search API](https://api.search.brave.com/) key for `decant search` (free tier covers light use; paid tier is $5/1k requests).

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

**Re-copy after every `git pull`** — the skill contract changes pre-1.0. The CLI resolves through the installed package, so `uv tool install .` (or `--editable`) keeps `decant <subcommand>` in sync.

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
  },
  "meta": {
    "...": "...",
    "model": "qwen3:32b",
    "tier": "accurate"
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

URLs are fetched sequentially (no parallelism — see [project notes](#project-notes)). The output is a JSON array in the same order as the arguments. In batch mode a summary line is printed to stderr (`2 succeeded, 1 failed (run completed in 3.4s)`), and the exit code follows this table:

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
| `--fast` | Use `ollama.fast_model`. Already the default when `fast_model` is configured; useful for scripts that want to be explicit. Ignored when `--model` is also passed. Errors if no `fast_model` is configured. |
| `--accurate` | Use `ollama.model` (the accurate tier). Opt-in for the cases where extra latency pays off. Ignored when `--model` is also passed. Mutually exclusive with `--fast`. |
| `--terse` | Drop verbatim source quotes from summary output. Smaller and cheaper to feed back into a conversation; less auditable. Requires summary or both mode. |
| `--no-cache` | Bypass cache for both read and write. |
| `--timeout SECONDS` | Per-URL fetch timeout (default from `fetch.request_timeout_s`). |
| `--allow-partial` | In batch mode, exit 0 if at least one URL succeeded. No effect on single-URL runs. |
| `--verbose` | Emit per-stage timings on stderr. |

### Fast vs. accurate model tiers

`decant` knows two model slots: `ollama.model` (accurate) and `ollama.fast_model` (optional, smaller). Default is `fast_model` when set, else `ollama.model`. `--accurate`, `--fast`, and `--model X` override in that priority. `meta.tier` reports which slot ran. The benchmark in `docs/extraction-benchmark.md` shows fast within ~5 points of recall and ~30% faster on typical pages. See [SPEC.md](./SPEC.md) for the full resolution table.

### Terse summary mode

```bash
uv run decant url https://example.com/article --question "What is X?" --terse
```

Each finding keeps `context` and `relevance` but drops the verbatim `quote`. `meta.terse: true` confirms it. Lean-and-cheap; less auditable. Rejected with `--mode extract`.

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

Different question/model/terse settings get separate entries; toggling them never returns stale results. `--mode both` writes the full payload plus single-mode projections so follow-up `extract`/`summary` calls hit cache. TTL defaults to 24 hours. See [SPEC.md](./SPEC.md) for the cache-key formula.

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
  fast_model: qwen3:8b                   # optional; when set, becomes the summary-mode default (use --accurate to opt out)
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

## Project notes

**Polite scraping policy** (non-negotiable, hardcoded):

- **User-Agent** identifies itself honestly with a contact email: `Decant/<version> (+mailto:<contact_email>)`. `decant url` refuses to run without one set.
- **robots.txt** is checked once per host per run. A disallow for our UA is fatal — there is **no** `--ignore-robots` flag.
- **Sequential fetches.** One URL at a time. No concurrency.
- **No retries on 4xx** other than 429. Single retry on 5xx/429 with 2-second backoff.
- **No cookies persisted across runs.** Each invocation gets a fresh browser context.

**Development**:

```bash
uv sync --extra dev
uv run pytest -q                              # ~70 tests, ~0.3s
DECANT_E2E=1 uv run pytest tests/test_fetch.py # adds the real-browser test
```

Implementation under `src/decant/`. Pipeline, error codes, and module roles are documented in [SPEC.md](./SPEC.md), the design-of-record.

## License

MIT — see [LICENSE](./LICENSE).
