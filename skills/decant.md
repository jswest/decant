---
name: decant
description: |
  Fetch URLs through the local `decant` CLI instead of WebFetch. Returns
  clean markdown or LLM-distilled findings rather than raw HTML, saving
  10k–50k context tokens per page. Reach for this whenever the user
  hands you a URL.
---

# decant

`decant` is a local-first CLI installed on the user's machine. It does
the URL-fetch work locally (Playwright + content extraction + optional
Ollama summarization) and returns a compact JSON payload, so you don't
have to load the full HTML into your context window.

## When to use

Use it whenever the user gives you a URL — to read documentation, dig
into an article, gather supporting context for a coding task, etc.

Don't use it for:
- File paths, local docs, or anything that isn't an `http`/`https` URL.
- Pages behind interactive auth — every `decant` run uses a fresh
  browser context, so cookies don't carry over.
- A page the user has *already* given you the contents of.

## How to call it

Always invoke via the Bash tool. Output is a JSON object on stdout
(or a JSON array if multiple URLs). Exit code is non-zero on any
error.

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
  surrounding `context`, and `relevance` ∈ {high, medium, low}.

Prefer summary mode when the user has a *specific* question. Prefer
extract mode when they want the whole page or you'll be doing your own
analysis.

### Both — markdown and findings in one response

    decant url <URL> --question "..." --mode both

Useful when you want findings now but might need the full markdown for
follow-up questions in the same turn.

### Multiple URLs

    decant url <URL1> <URL2> <URL3>

Output is a JSON array in argument order. Fetches are sequential (no
parallelism — see "polite scraping" below). Pass them all in one
invocation so they share a browser context.

### Useful flags

- `--no-cache` — bypass the 24h cache. Use when you need a fresh fetch
  (e.g. the page just changed).
- `--model MODEL` — override the default Ollama model for this run.
- `--timeout SECONDS` — per-URL fetch ceiling.

## Reading the output

Parse with `jq` (or any JSON tool). Examples:

    decant url <URL> | jq -r .extract.markdown
    decant url <URL> --question "..." | jq -r .summary.answer
    decant url <URL> --question "..." | jq '.summary.findings[] | "\(.relevance): \(.quote)"'

For multi-URL runs, iterate the array:

    decant url <URL1> <URL2> | jq -r '.[] | .title + " — " + .url'

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
| `invalid_url` | The URL didn't parse or isn't http(s). Re-check what you sent. |

## Polite scraping — implications for you

- decant honors `robots.txt` and has **no** `--ignore-robots` flag. If
  you hit `robots_disallowed`, don't try to work around it.
- Fetches are sequential, ~1–3 s/page typical. Batch URLs in one call
  rather than calling decant repeatedly.
- Results are cached 24h by default. Re-asking the same question
  against the same URL is free; the response will have `meta.cached:
  true`.

## Quick reference

    decant url <URL>                              # clean markdown
    decant url <URL> --question "..."             # LLM findings
    decant url <URL> --question "..." --mode both # both
    decant url <URL1> <URL2> <URL3>               # batch (sequential)
    decant cache stats                            # inspect cache
    decant cache clear                            # wipe cache
    decant version                                # version + paths
