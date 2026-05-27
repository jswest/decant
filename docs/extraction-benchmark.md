# Extraction benchmark — decant vs WebSearch

A repeatable procedure for measuring whether decant is improving on
WebSearch for the same questions. Run before/after any extractor swap
(e.g. issue #6) to see real token-cost and recall deltas.

## What we measure

For each (URL, question, ground-truth-facts) tuple in the corpus, run
three retrievers and score each output on four axes:

| Metric | Definition |
|---|---|
| **Token count** | Approximate Claude input tokens the output would cost if pasted into a conversation. |
| **Recall** | Of the listed ground-truth facts, how many appear in the output (exact or paraphrased). Scored as `hit_count / total_facts`. |
| **Noise** | Approximate share of the output that is chrome, boilerplate, or unrelated content. 1 = lean, 5 = mostly junk. |
| **Time** | Wall-clock seconds from invocation to usable output. For decant, time the CLI call. For WebSearch, time the tool call. Measure cold (no cache) — see "Caveats" on the cache-warm case. |

Token count is the hard cost. Recall is correctness. Noise is what we
trade away when extract mode bloats. Time matters because a 10× cheaper
answer that takes 20s isn't always the right choice when WebSearch
returns in 4s.

## Retrievers under test

1. **decant summary** — `decant url <URL> --question "..."`
   Reads `.summary.answer` + concatenated `.summary.findings[].quote`.
2. **decant extract** — `decant url <URL>`
   Reads `.extract.markdown`.
3. **WebSearch** — the Claude Code `WebSearch` tool with a query phrased
   to find the same canonical page. Read the synthesized response in
   full (snippets + summary).

## Corpus

Stable URL, fixed question, hand-curated ground-truth facts. Add cases
sparingly — every addition invalidates aggregate comparisons against
prior runs.

| # | Page type | URL | Question | Ground-truth facts |
|---|---|---|---|---|
| 1 | API reference (dense, JS-rendered) | `https://api-dashboard.search.brave.com/app/documentation/web-search/get-started` | What are the required parameters and authentication for the Brave Web Search API? | `q` is required; `X-Subscription-Token` header for auth; base URL `https://api.search.brave.com/res/v1/`; q is ≤400 chars / ≤50 words |
| 2 | Pricing / marketing | `https://brave.com/search/api/` | What does the Brave Search API cost and what are the rate limits? | $5 per 1,000 requests; $5/month free credits; 50 QPS on Search plan; 2 QPS on Answers plan; Enterprise = custom |
| 3 | Prose article / blog | `https://nginx.org/en/docs/http/load_balancing.html` | What load-balancing methods does nginx support and how do you configure weighted round-robin? | Methods: round-robin (default), least-connected, ip-hash, generic hash, least time (commercial); `weight=N` on `server` directive inside `upstream`; example shows `server backend1.example.com weight=5;` |
| 4 | Reference / spec | `https://www.rfc-editor.org/rfc/rfc7231` | What HTTP methods does RFC 7231 define and which are idempotent? | Defines GET, HEAD, POST, PUT, DELETE, CONNECT, OPTIONS, TRACE; idempotent: GET, HEAD, PUT, DELETE, OPTIONS, TRACE (not POST or CONNECT); safe: GET, HEAD, OPTIONS, TRACE |
| 5 | Legal / terms | `https://opensource.org/license/mit` | What does the MIT license require and what does it disclaim? | Requires copyright notice + permission notice in copies; permits use/copy/modify/merge/publish/distribute/sublicense/sell; disclaims warranty of merchantability, fitness for purpose, non-infringement; "AS IS" |

If you change the corpus, bump the version in the results header
(`Corpus v2`, etc.) so prior results aren't accidentally compared to
new cases.

## Procedure

For each row in the corpus:

Wrap each call in `time` (or capture wall-clock manually) so the
"Time" metric lands in the same pass as the text capture. Use
`--no-cache` on decant runs so you measure a cold fetch rather than a
sub-second cache hit.

### 1. decant summary

```bash
time decant url <URL> --question "<question>" --no-cache \
  | jq -r '"\(.summary.answer)\n\n" + ([.summary.findings[].quote] | join("\n\n"))' \
  > /tmp/bench-summary.txt
```

> **Note (issue #23):** since the default flip, `decant url --question` hits
> the **fast tier** when `ollama.fast_model` is configured. To benchmark the
> accurate tier explicitly, pass `--accurate`. To compare both tiers in the
> same pass, run the command twice — once bare, once with `--accurate` —
> and record both rows (label the retriever `decant summary (fast)` /
> `decant summary (accurate)`). The `meta.tier` field in the response
> confirms which tier ran.

### 2. decant extract

```bash
time decant url <URL> --no-cache | jq -r .extract.markdown > /tmp/bench-extract.txt
```

### 3. WebSearch

Invoke the WebSearch tool with a query that should surface the
canonical page (not the URL verbatim — phrase it as a question a user
would type). Time the tool call (the harness wrapping it, or note
wall-clock before/after). Capture the entire returned synthesis as
plain text into `/tmp/bench-websearch.txt`.

### 4. Score each output

- **Token count.** Quick estimate: `wc -c /tmp/bench-*.txt` and divide
  by 4. For precision (matters less than the relative numbers), pipe
  through Anthropic's tokenizer:

  ```bash
  python -c "from anthropic import Anthropic; import sys; \
    print(Anthropic().messages.count_tokens(model='claude-opus-4-7', \
    messages=[{'role':'user','content':sys.stdin.read()}]).input_tokens)" \
    < /tmp/bench-summary.txt
  ```

- **Recall.** Open the file, walk down the ground-truth list, mark each
  fact present (Y) or missing (N). Record `Y_count / total`.

- **Noise.** Eyeball the output. Rate 1–5:
  - **1** — every paragraph contributes to the answer.
  - **3** — answer is there, surrounded by nav/footer/related-links chrome.
  - **5** — answer is buried in mostly-unrelated content, or absent.

  Three-pass calibration: do all corpus rows in one sitting so the
  scale stays consistent across retrievers within a run.

## Results format

Append a section to this file (or a sibling `extraction-results-N.md`
under `/docs/REPORT*` since that prefix is gitignored) using the
template below.

```
## Run YYYY-MM-DD — <branch/commit>

Corpus v1. decant on `<git rev>`. Notes: <anything affecting the run>.

| # | Retriever | Tokens | Recall | Noise | Time (s) | Notes |
|---|---|---|---|---|---|---|
| 1 | decant summary |   |   |   |   |   |
| 1 | decant extract |   |   |   |   |   |
| 1 | WebSearch |   |   |   |   |   |
| 2 | decant summary |   |   |   |   |   |
| ... |

### Aggregate

| Retriever | Mean tokens | Mean recall | Mean noise | Mean time (s) |
|---|---|---|---|---|
| decant summary |   |   |   |   |
| decant extract |   |   |   |   |
| WebSearch |   |   |   |   |

### Notable deltas vs prior run

- ...
```

## Caveats

- **WebSearch drift.** WebSearch's index changes; the same query a
  month from now may return different sites. Either re-run WebSearch
  on every comparison (treats it as a moving baseline) or freeze the
  WebSearch row from a chosen anchor run and only re-run decant.
- **Noise is judgment.** Different humans will score the same output
  ±1 point. Track *deltas within a run*, not absolute noise across
  runs scored by different people.
- **Token estimates are tokenizer-specific.** `chars / 4` and Claude's
  tokenizer can disagree by 10–15%. Don't mix methods within a single
  results table.
- **Caching skews timing.** If you re-run a URL within 24h, decant
  will serve from cache (`meta.cached: true`) in sub-second time. The
  benchmark procedure uses `--no-cache` so the Time column reflects a
  real cold fetch. If you want the steady-state-cache cost for
  comparison, run a second pass without `--no-cache` and label it
  "warm" — but don't average warm and cold times together.

## When to re-run

- After any extractor change (issue #6 Readability swap, the LLM
  fallback in #7, future swaps).
- After distill/summary-prompt changes that could affect summary mode
  output length.
- Quarterly, to catch drift in WebSearch quality without a code
  trigger.
