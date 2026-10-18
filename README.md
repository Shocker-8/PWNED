# PWNED — Predictive Wordlist Network & Entity Discovery

> An LLM-guided custom password **wordlist generator** for **authorized** penetration testing.
> You describe a target in plain language; a **local** Gemma model interviews you, reasons about
> the clues, and a deterministic engine expands them into a bounded, de-duplicated candidate list.

```
 ██████╗ ██╗    ██╗███╗   ██╗███████╗██████╗
 ██╔══██╗██║    ██║████╗  ██║██╔════╝██╔══██╗
 ██████╔╝██║ █╗ ██║██╔██╗ ██║█████╗  ██║  ██║
 ██╔═══╝ ██║███╗██║██║╚██╗██║██╔══╝  ██║  ██║
 ██║     ╚███╔███╔╝██║ ╚████║███████╗██████╔╝
 ╚═╝      ╚══╝╚══╝ ╚═╝  ╚═══╝╚══════╝╚══════╝
```

Everything runs **locally and offline**. The tool only *produces a list*; it never attempts a login,
scans a network, or talks to any remote service.

---

## The idea

Classic wordlist builders like [bopscrk](https://github.com/r3nt0n/bopscrk) take a handful of words
about a target and mutate them (case, leet, dates, combinations). PWNED keeps that deterministic
core but puts a **local LLM in the interviewer's seat**, because real recon isn't a list of words —
it's *characteristics*:

> *"fat male, 39, name nuno, from Lisbon, Benfica fan, unemployed"*

A plain keyword tool does nothing with "fat" or "Benfica fan". Gemma reasons about them:

| You say | The model derives |
|---|---|
| `fat` | `gordo`, `big`, `chunky`, `large` |
| `Lisbon / Benfica` | `lisboa`, `benfica`, `slb`, `eagles`, `benfiquista` |
| `unemployed` | `desempregado` |
| `39 years old` | birth year `1987` (from the current year) |

It also tracks the **languages** in play (here `en` + `pt`) and keeps using them, and it asks the
follow-up questions *you* might not think of (partner, pets, anniversaries, employer, teams, bands…).
Python then owns everything deterministic: validation, limits, de-duplication, and the output file.

---

## Requirements

- **Python 3.12+**
- **[LM Studio](https://lmstudio.ai/)** running its local server (default `http://127.0.0.1:1234`)
  with a chat model loaded. The showcase model is **`google/gemma-4-12b-qat`**, but any local model works
  (see [Choosing a model](#choosing-a-model)).

## Setup

```sh
# 1. create a venv and install
python3.12 -m venv .venv
.venv/bin/pip install -e .

# 2. start LM Studio, load a model, and start its local server (Developer tab)

# 3. run it
.venv/bin/python main.py
```

That's it — `main.py` is the single entrypoint.

## Choosing a model

The app reads `opencode.json` and resolves its runtime model with clear precedence:
`runtime_model` → `model` → `small_model`. `model`/`small_model` are only used when they name the
default Gemma model (so the coding agent's own model is never picked by accident). To point the app
at **any** model you have loaded in LM Studio, set `runtime_model`:

```json
{
  "runtime_model": "lmstudio/google/gemma-4-12b-qat",
  "provider": {
    "lmstudio": { "options": { "baseURL": "http://127.0.0.1:1234/v1" } }
  }
}
```

> **Note on reasoning models:** some local builds (Gemma QAT included) spend a few hundred tokens
> "thinking" before the JSON answer. The client detects this and automatically grows the output
> budget; if a model reasons without ever answering, it says so plainly instead of hanging.

---

## Using it

```
Select an option:
  1. Account Username & Password        (not yet implemented)
  2. Account Username only              (not yet implemented)
  3. Account Password only              ← custom password wordlist
  4. Wi-Fi passphrase                   ← custom passphrase wordlist
  5. Account & entity discovery (OSINT) (not yet implemented)
  6. Resume previous checkpoint         ← continue a saved session
  q. Quit
```

**Options 3, 4 and 6 are fully wired.** A run looks like this:

1. **Describe the target** in your own words (multi-line; blank line finishes).
2. **Gemma interviews you** — one adaptive follow-up at a time. Type an answer, `skip` a question,
   or `stop` to jump straight to generation. A progress bar shows an estimate while the model thinks.
3. **Plain y/n knobs** (no model calls): combine words? case transforms? leet? plus min/max length
   (defaults shown — account `1–40`, Wi-Fi `8–63`).
4. Optionally **merge existing wordlist files** (comma-separated paths); they're length-filtered in.
5. The **engine writes the wordlist** — one candidate per line — to a `.txt`, bounded, de-duplicated
   and length-filtered, and reports counts, languages and where it stopped.

Every run also saves a `<output>.session.json`. **Option 6 (Resume)** reloads it, lets you add more
target info, and regenerates. Output and session filenames **auto-number** (`name_1.txt`) so a rerun
never overwrites earlier work.

### Example output

From the Lisbon/Benfica target above (combine + case + leet on):

```
nuno
Nuno
NUNO
nun0
benfica
benfiquista
slb
gordo
nunobenfica
nuno-benfica
nuno1987
Benfica1987
...
```

---

## Why it's a solid open-source AI project

- **Local-first & private.** The only AI is an open-weights model you run yourself in LM Studio. No
  keys, no cloud, no data leaving the machine.
- **The model does the thinking; Python keeps the guarantees.** The LLM is confined to proposing
  words and questions; a deterministic, heavily-tested engine owns validation, bounds, de-dup, byte
  limits and file writes. Malformed or empty model output fails honestly — **never** a silent fallback.
- **Reproducible & bounded.** Candidate caps, attempt caps, byte caps, a cooperative time budget, a
  SQLite-backed exact de-duplicator with bounded memory, and resumable checkpoints.
- **Well tested.** 200+ offline tests (`pytest`), plus a live model check that's skipped only when
  LM Studio is unreachable.

## Architecture

| Module | Role |
|---|---|
| `main.py` | Single entrypoint: logo, menu, routing |
| `candidatelab/wordlistgen.py` | Target interview (Gemma), language capture, knobs, session save/resume |
| `candidatelab/engine.py` | Bounded generation: seeds → variants → combinations → filter → dedup → file |
| `candidatelab/transforms.py` | Case / leet / affix mutations |
| `candidatelab/combinations.py` | Bounded word + number + date combinations |
| `candidatelab/dedupe.py` | Exact de-dup (in-memory or SQLite), order-preserving |
| `candidatelab/checkpoint.py` | Versioned checkpoints + deterministic resume |
| `candidatelab/policyaudit.py` | Separate defensive CLI: `candidatelab policy-audit` |

## Tests

```sh
PYTHONPATH=src .venv/bin/python -m pytest -q --ignore=tests/test_policyaudit_live.py   # offline
PYTHONPATH=src .venv/bin/python -m pytest tests/test_policyaudit_live.py               # live model
```

---

## Responsible use

PWNED is for **authorized** security testing — your own accounts, systems you have written permission
to assess, and CTF/lab targets. It generates *candidate* strings for a separate tool you are
authorized to use; it performs no authentication, no network access, and no OSINT lookups. Don't use
it against people or systems you don't have permission to test.

## Roadmap (not yet implemented)

- **1. Account Username & Password** and **2. Account Username only** — username/credential-pair modes
- **5. Account & entity discovery (OSINT)** — pull public signals to seed the interview
- **LLM-authored filters** for merged input wordlists (today they're length/charset filtered)

## AI disclosure

Built with the help of **Claude Code** (Anthropic). The shipped tool runs a **local** model via
LM Studio (`google/gemma-4-12b-qat` by default) — that model is the product's AI, not a cloud API.
