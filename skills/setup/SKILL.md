---
name: setup
description: |
  Install and configure the decant CLI end to end for the user: uv,
  decant itself, headless Chromium, and ~/.decant/config.yaml. Use when
  the user asks to set up or install decant, or when a decant command
  fails with `command not found`, `config_missing` or `browser_missing`.
---

# Set up decant

Do the whole install yourself. The user may not be technical: explain
what you're doing in a sentence or two at each step, and only ask them
for the things listed in step 5. Run each step with the Bash tool and
read its output before moving on.

## 1. Make sure `uv` is installed

    command -v uv

If it's missing, tell the user you're installing uv (a Python package
manager from Astral) and run the official installer:

- macOS / Linux: `curl -LsSf https://astral.sh/uv/install.sh | sh`
- Windows (PowerShell): `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"`

The installer puts `uv` in `~/.local/bin`, which the current shell may
not have on `PATH` yet. If `command -v uv` still fails, call it as
`~/.local/bin/uv` for the rest of this session.

uv downloads its own Python if needed, so don't install Python
separately.

## 2. Install the decant CLI

Use a **10-minute Bash timeout** for this step and step 4. Both
download a lot on first run.

    uv tool install git+https://github.com/jswest/decant

If decant is already installed, upgrade instead:

    uv tool upgrade decant

## 3. Make sure `decant` is on PATH

    command -v decant

If it isn't found, run `uv tool update-shell` (so future terminals pick
it up) and call decant as `"$(uv tool dir --bin)/decant"` for the rest
of this session.

## 4. Install the browser and check status

    decant setup

This downloads headless Chromium (~150 MB, one time) and prints a JSON
status report:

- `browser.ok` — Chromium launches. If `false` on Linux, run the command
  in `browser.fix` (it needs `sudo`; tell the user why first), then run
  `decant setup` again.
- `config` — `"ok"` or `"missing"`.
- `ollama.reachable` / `ollama.models` — whether a local Ollama server
  is running and which models it has.
- `brave_key` — whether a Brave Search key is already saved.

## 5. Ask the user two things

Ask both in one message:

1. **Contact email.** decant puts it in its User-Agent so websites know
   who's fetching their pages. It's required. If `git config
   user.email` returns something, offer that as the default. If
   `contact_email` in the status report is already set, ask whether to
   keep it.
2. **Brave Search API key (optional).** Say plainly that they can skip
   it: you'll use your built-in web search to find pages and decant to
   read them. A key (from https://api.search.brave.com/) only makes
   searching cheaper in tokens. Skip this question if `brave_key` is
   already `true`.

## 6. Decide on Ollama

Ollama is a free app that runs AI models on the user's own machine.
decant uses it to answer questions about a page locally
(`decant url ... --question`). Without it, decant still returns clean
page text.

- **`ollama.reachable` is true and `models` has a chat model:** ignore
  embedding models (names containing `embed`). Pick the largest
  remaining model as `--model`. If there's also a model of
  roughly 8B parameters or smaller, pass it as `--fast-model`. Pass
  `--ollama` to make sure summaries are on.
- **Otherwise:** pass `--no-ollama`. Tell the user summaries are off
  and that they can turn them on later by installing Ollama from
  https://ollama.com, running `ollama pull qwen3:8b`, then running
  `decant config --ollama --model qwen3:8b`.

## 7. Write the config

    decant config --email EMAIL [--brave-key KEY] [--ollama --model M --fast-model F | --no-ollama]

It prints the saved settings as JSON. Every flag is optional except
`--email` on the very first run.

## 8. Smoke test

    decant url https://example.com

Confirm `.extract.markdown` is non-empty. If Ollama is on, also run:

    decant url https://example.com --question "What is this page for?"

and confirm `.summary.answer` is present.

## 9. Report back

Tell the user in plain language what's installed, what's on or off
(summaries, Brave search) and that they can now just ask you to read a
URL or look something up. Don't paste the JSON.
