---
name: decant
description: |
  Fetch URLs (and search the web) through the local `decant` CLI instead
  of WebFetch. Returns clean markdown, LLM-distilled findings, or
  Brave-LLM-Context search results — all in compact JSON. Reach for this
  whenever the user hands you a URL or asks you to look something up.
---

# decant

`decant` is a local-first CLI installed on the user's machine. It does
the URL-fetch work locally (Playwright + content extraction + optional
Ollama summarization) and returns a compact JSON payload, so you don't
have to load the full HTML into your context window.

## When to use

- **User gives you a URL** → use `decant url`. Documentation, articles,
  context for a coding task.
- **User asks you to find, search, or research something** without
  naming a specific page → use `decant search` to triage candidate URLs,
  then `decant url` on the ones worth reading.

Don't use it for:
- File paths, local docs, or anything that isn't an `http`/`https` URL.
- Pages behind interactive auth — every `decant` run uses a fresh
  browser context, so cookies don't carry over.
- A page the user has *already* given you the contents of.

## How to call it

Always invoke via the Bash tool. Output is a JSON object on stdout
(or a JSON array if multiple URLs). In batch mode, inspect each
element's `code` field rather than relying solely on exit status —
see the exit-code table under "Multiple URLs" below.

### Just the clean markdown

    decant url <URL>

The text you want is `.extract.markdown`. The page title is `.title`.

### With a research question (distilled findings)

    decant url <URL> --question "<the user's question, verbatim>"

This skips the raw markdown and returns structured findings from a
local LLM. Read:
- `.summary.answer` — a 1–3 sentence direct answer, or `null` if the
  page doesn't answer the question.
- `.summary.page_topic` — one-sentence description of the page.
- `.summary.findings[]` — up to 8 items, each with a verbatim `quote`,
  surrounding `context`, and `relevance` ∈ {high, medium, low}. When
  `--terse` is set, the `quote` field is dropped from each finding (the
  rest of the shape is unchanged) — see "Terse mode" below.

Prefer summary mode when the user has a *specific* question. Prefer
extract mode when they want the whole page or you'll be doing your own
analysis.

### Terse mode — drop the verbatim quotes

    decant url <URL> --question "..." --terse

Cuts `findings[].quote` from the response. You still get the answer,
the page topic, and each finding's `context` (section/heading) +
`relevance`, but not the source text. Roughly 30–60% smaller for typical
pages, since quotes are the biggest contributor to summary tokens.

Reach for `--terse` when:
- The user wants the answer, not the citations (e.g. "what's the rate
  limit on the Brave API?").
- You're chaining several `decant url` calls in one turn and want to
  keep each response small.
- You'll be reasoning over the answer yourself rather than showing the
  user verbatim source.

**Don't** use `--terse` when:
- The user asked for quotes, citations, or wants to see the source
  text.
- The question is legal, regulatory, or compliance-flavored — keep the
  verbatim quotes so the answer is auditable.

`--terse` combines with `--fast` for the cheapest summary call;
`--accurate` without `--terse` is the audit/legal setting. `--terse`
can't be used with `--mode extract` (extract mode doesn't call the
LLM). Check `.meta.terse` (boolean) on every summary/both response to
confirm which shape you got back.

### Both — markdown and findings in one response

    decant url <URL> --question "..." --mode both

Useful when you want findings now but might need the full markdown for
follow-up questions in the same turn.

### Multiple URLs

    decant url <URL1> <URL2> <URL3>

Output is a JSON array in argument order. Fetches are sequential (no
parallelism — see "polite scraping" below). Pass them all in one
invocation so they share a browser context.

In batch mode a summary line is printed to stderr (`2 succeeded, 1
failed (run completed in 3.4s)`), and exit code follows this table:

| Scenario | Default exit | With `--allow-partial` |
|---|---|---|
| All URLs succeeded | 0 | 0 |
| Some URLs succeeded, some failed | 1 | 0 |
| All URLs failed | 1 | 1 |

Default fail-strict is safer for automation. Use `--allow-partial`
when one bad URL shouldn't kill downstream shell-pipeline work.

### Useful flags

- `--no-cache` — bypass the 24h cache. Use when you need a fresh fetch
  (e.g. the page just changed).
- `--model MODEL` — override the default Ollama model for this run.
- `--fast` / `--accurate` — pick the model tier explicitly. When the
  user has configured `ollama.fast_model`, the fast model is the
  default for `--question` runs (this changed in issue #23 — the
  fast/accurate gap is small on typical pages and fast is ~30%
  quicker). Reach for `--accurate` when the question demands rigor:
  legal terms, license text, regulatory/compliance details, dense
  reference specs, or anything where a missed nuance costs the user
  more than the extra ~10s. `--fast` is mostly a no-op against the
  current default but stays valid for scripts that want to pin the
  tier. `--model` overrides both. Check `meta.tier` to see which
  model was used (`"fast"`, `"accurate"`, or `"explicit"`). The two
  flags are mutually exclusive.
- `--terse` — drop verbatim quote blocks from the summary (see "Terse
  mode" above). Combine with `--fast` for the cheapest summary call.
- `--timeout SECONDS` — per-URL fetch ceiling.
- `--allow-partial` — in batch mode, exit 0 if at least one URL
  succeeded. No effect on single-URL runs.

## Searching the web

When the user asks you to find something rather than read a specific
URL, use `decant search` to get candidate URLs, then `decant url` on
the promising ones.

    decant search "<query>"

Output has `.results[]` with `url`, `title`, `hostname`, `age`, and
`snippets[]` per item.

**The canonical two-step pattern:**

    # 1. Triage
    decant search "qwen3 license terms"

    # 2. Read the URLs that look promising
    decant url <URL1> <URL2> --question "<the user's actual question>"

Guidance:
- **Use search snippets for triage, not for the final answer.** The
  default `--token-budget` (4096) gives you enough to pick which URLs
  are worth a full read; it's not enough to skip the second step. Don't
  raise `--token-budget` unless the user explicitly asks for more.
- **Pick 2–4 URLs to follow up on**, not 10. Each `decant url` fetch
  costs real time.
- **Don't fall back to web scraping** if search fails. Tell the user
  what went wrong.

Search-specific flags worth knowing:
- `--top N` — max URLs returned (default 10).
- `--freshness pd|pw|pm|py|<range>` — useful when the user asks about
  recent events. `pd`=past day, `pw`=past week, `pm`=past month,
  `py`=past year.
- `--country XX`, `--lang xx` — override defaults when the query is
  region/language-specific.

Search-specific errors:
- `search_no_api_key` — no Brave key configured. Tell the user to run
  `decant config` and add one, or set `BRAVE_SEARCH_API_KEY`.
- `search_rate_limited` — wait the `details.retry_after` seconds and
  retry (Brave's free tier allows 1 req/sec).
- `search_bad_query` — the query is too long, too many words, or empty.
  Trim it and retry.

## Reading the output

Parse with `jq` (or any JSON tool). Examples:

    decant url <URL> | jq -r .extract.markdown
    decant url <URL> --question "..." | jq -r .summary.answer
    decant url <URL> --question "..." | jq '.summary.findings[] | "\(.relevance): \(.quote)"'
    decant search "..." | jq -r '.results[] | .title + " — " + .url'

For multi-URL runs, iterate the array:

    decant url <URL1> <URL2> | jq -r '.[] | .title + " — " + .url'

## Watch for soft 404s

Every `decant url` response includes `meta.soft_404.verdict`. Check it
before trusting the result, especially when the page came from a
documentation site, an SPA, or any URL the user typed from memory.

- **`unlikely`** — proceed normally.
- **`possible`** — only `extract_too_short` tripped. The page rendered
  but came back near-empty. Could be a real stub page, a paywalled
  article, or a JS-only app that didn't finish hydrating. Mention this
  to the user before drawing strong conclusions from the content.
- **`likely`** — a strong signal tripped. Treat the result as suspect.
  Typical follow-ups:
  - If `title_contains_404`: tell the user the page returned a 404 in
    disguise. Don't quote its content as if it answers their question.
  - If `canonical_mismatch`: the site rendered something other than
    what you requested. Show the user what was actually fetched
    (`final_url` or the canonical URL from the page) and ask whether
    they want that, or try a different URL.

Quick jq pattern:

    decant url <URL> | jq -r '.meta.soft_404 | "\(.verdict): \(.reasons | join(", "))"'

## Errors

Errors come back as JSON objects with a `code` field instead of a
`title`/`extract`/etc. Handle these:

| Code | What to do |
| --- | --- |
| `robots_disallowed` | Site blocks our UA. Tell the user; suggest they fetch it themselves if it's important. |
| `fetch_failed` | Transient or hard fetch error. Show the message; retry once with `--no-cache` if it looks transient. |
| `extract_empty` | Page rendered but no main content found (likely a JS-only app or a blocker page). Tell the user. |
| `ollama_unavailable` / `ollama_timeout` | Local LLM is down or slow. If you only need the text, re-run without `--question` to get extract mode. |
| `ollama_bad_json` | Rare. Retry once; if it still fails, fall back to extract mode and reason yourself. |
| `config_missing` | User hasn't set decant up. Tell them to run `decant config`. |
| `config_missing_fast_model` | You passed `--fast` but the user hasn't configured `ollama.fast_model`. Either drop `--fast` (falls back to the accurate model) or tell them to set it via `decant config`. |
| `invalid_url` | The URL didn't parse or isn't http(s). Re-check what you sent. |

## Polite scraping — implications for you

- decant honors `robots.txt` and has **no** `--ignore-robots` flag. If
  you hit `robots_disallowed`, don't try to work around it.
- Fetches are sequential, ~1–3 s/page typical. Batch URLs in one call
  rather than calling decant repeatedly.
- Results are cached 24h by default. Re-asking the same question
  against the same URL is free; the response will have `meta.cached:
  true`. The key shape is `url + mode + question + model` for summary
  and both modes (with a `|terse` suffix when `--terse` is set, so terse
  and full responses cache separately); extract mode keys on `url`
  alone (model-, question-, and terse-independent). **Running `--mode
  both` warms the extract and summary caches too** — if you might want
  both views, do `--mode both` first so follow-up single-mode calls hit
  cache.

## Quick reference

    decant url <URL>                              # clean markdown
    decant url <URL> --question "..."             # LLM findings (fast model if configured, else accurate)
    decant url <URL> --question "..." --accurate  # LLM findings (force accurate model)
    decant url <URL> --question "..." --fast      # LLM findings (force fast model)
    decant url <URL> --question "..." --terse     # LLM answer without verbatim quotes (smaller)
    decant url <URL> --question "..." --mode both # both
    decant url <URL1> <URL2> <URL3>               # batch (sequential)
    decant search "<query>"                       # find candidate URLs
    decant search "<q>" --top 5 --freshness pw    # narrower, recent-only
    decant cache stats                            # inspect cache
    decant cache clear                            # wipe cache
    decant version                                # version + paths

## Tuning keep_alive for two-model workflows

When using both `model` and `fast_model` (see tiered distillation), models may
evict each other if GPU memory is limited. To reduce reload latency:

- Set `ollama.keep_alive: -1` if only one model is in play and GPU memory is ample.
- Set `ollama.keep_alive: 30s` or similar short duration when alternating between
  tiers on a single-GPU machine so the outgoing model unloads quickly.
- The default `5m` is safe for single-model use and matches Ollama's built-in default.
