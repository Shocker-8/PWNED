<!-- PWNED — Predictive Wordlist Network & Entity Discovery -->

```
 ██████╗ ██╗    ██╗███╗   ██╗███████╗██████╗
 ██╔══██╗██║    ██║████╗  ██║██╔════╝██╔══██╗
 ██████╔╝██║ █╗ ██║██╔██╗ ██║█████╗  ██║  ██║
 ██╔═══╝ ██║███╗██║██║╚██╗██║██╔══╝  ██║  ██║
 ██║     ╚███╔███╔╝██║ ╚████║███████╗██████╔╝
 ╚═╝      ╚══╝╚══╝ ╚═╝  ╚═══╝╚══════╝╚══════╝
   PREDICTIVE WORDLIST NETWORK & ENTITY DISCOVERY
```

# PWNED

**PWNED** turns plain-language intelligence about a target into a **bounded,
deduplicated candidate wordlist** for authorized password audits. A locally
hosted LLM (served by **LM Studio**) conducts the interview and extracts useful
base words, numbers and dates; deterministic Python then expands, filters and
writes the final list. Everything runs **offline on your machine** — no cloud, no
telemetry, and the tool never attempts a login itself.

> Built for Hacktoberfest. Retro terminal UX, local inference, and a strict split
> between *what the model suggests* and *what the code is allowed to do*.

---

## Why it's interesting

- **Local LLM, real inference.** The model actually drives the interview through
  LM Studio's OpenAI-compatible API — it isn't a cosmetic greeting bolted onto a
  fixed script. Structured JSON output is validated on every call.
- **Model proposes, code decides.** The LLM only *suggests* base words; Python
  keeps strictly to what the operator typed (nothing invented), owns all limits,
  and performs every transformation and file write.
- **Two modes.** A *predictive wordlist generator* (the main event) and a
  read-only *password-policy auditor* that emits a synthetic test corpus plus a
  report explaining each case's purpose and expected validation result.
- **Resumable.** Long interviews are checkpointed to JSON; pick up later, add more
  intel, and regenerate. Output and session files auto-number so nothing is lost.

---

## How it works

```
   you ──describe target──► LM Studio (local LLM) ──JSON──►  interview controller
                                                              │  (validates, keeps
                                                              │   only grounded facts)
                                                              ▼
   plain y/n knobs ─────────────────────────────────►  generation engine
   (combine · case · leet · length · wordlists)             │  (expand → filter →
                                                              │   dedupe → bound)
                                                              ▼
                                                        wordlist.txt  +  session.json
```

- **LLM layer** (`src/candidatelab/wordlistgen.py`, `policyaudit.py`): talks to LM
  Studio, enforces a strict response schema, and discards any token the operator
  did not actually say.
- **Deterministic engine** (`engine.py`, `transforms.py`, `combinations.py`,
  `dedupe.py`): combinations, case/leet variants, numeric/date affixes,
  length/charset filtering, exact SQLite-backed deduplication, bounded output.

---

## Requirements

- **Python 3.12+**
- **[LM Studio](https://lmstudio.ai/)** running its local server
  (**Developer → Start Server**, default `http://127.0.0.1:1234`)
- A chat model loaded in LM Studio that supports **structured JSON output**.
  The reference model is `google/gemma-4-12b-qat`, but **any compatible local
  model works** — see [Choosing a model](#choosing-a-model).

---

## Setup

```sh
# 1. create the project virtualenv and install dependencies
python3 -m venv .venv
.venv/bin/python -m pip install .

# 2. in LM Studio: load a model, then Developer ▸ Start Server (port 1234)

# 3. run PWNED
.venv/bin/python main.py
```

`main.py` prepends `src/` to the path, so it always runs the current source — no
reinstall is needed after editing the code. The `pip install` step is only to
pull the dependency (Rich) and the optional `candidatelab` console command.

### Choosing a model

PWNED reads its runtime model from `opencode.json` (same folder as `main.py`).
Resolution order is **`runtime_model` → `model` → `small_model`**:

- **`runtime_model`** is an explicit override and may name **any** model you have
  loaded in LM Studio.
- `model` / `small_model` are only accepted when they name the default reference
  model, so an unrelated coding-assistant `model` is never picked by accident.

To use your own model, add one line pointing at its LM Studio id:

```jsonc
{
  "runtime_model": "lmstudio/your-model-id-here",
  "provider": {
    "lmstudio": {
      "options": { "baseURL": "http://127.0.0.1:1234/v1" }
    }
  }
}
```

The endpoint must be loopback HTTP (`127.0.0.1` / `localhost`). If the model or
server isn't reachable, PWNED says so clearly and never silently falls back to a
cloud model.

> **Note on speed/quality:** reasoning-heavy models (like the Gemma QAT default)
> spend extra tokens "thinking" before the JSON, so a call can take tens of
> seconds on modest hardware. A progress bar shows an estimate while you wait.

---

## Usage

Running `main.py` shows the banner and this menu:

```
Select an option:
  1. Account Username & Password        (not yet implemented)
  2. Account Username only              (not yet implemented)
  3. Account Password only
  4. Wi-Fi passphrase
  5. Account & entity discovery (OSINT tools)   (not yet implemented)
  6. Resume previous checkpoint
  q. Quit
```

**Options 3, 4 and 6 are fully working.** 1, 2 and 5 are stubs, clearly marked
`(not yet implemented)`.

### A generation run (option 3 or 4)

1. **Describe the target** in your own words (multi-line; blank line finishes).
2. The **model interviews you** one question at a time, extracting base words,
   numbers and dates — only ones present in your text. Type `skip` to pass a
   question, or `stop` to end the interview and generate immediately.
3. **Plain y/n knobs** (no model calls): combine words? case variants? leet
   substitutions? then minimum / maximum length (defaults shown — account `1–40`,
   Wi-Fi `8–63` via the WPA2 preset).
4. Optionally give **comma-separated paths to existing wordlists** to merge; they
   are length/charset-filtered into the output.
5. The engine writes a **`.txt` wordlist, one candidate per line**, bounded and
   deduplicated, plus a `.session.json` checkpoint.

Example (abridged) for *"male, 39, name john, from france, born 1990, dog rex"*:

```
john
John
J0hn
johnFrance
john-France
france1990
Rex1986
...
```

### Resuming (option 6)

Lists `*.session.json` files in the folder, reloads one, lets you add more target
intel, and regenerates. New output files **auto-number** (`name_1.txt`,
`name_2.txt`) so previous results are never overwritten.

### Policy-audit mode (CLI)

The read-only defensive auditor is available as a subcommand. It extracts a
password/Wi-Fi **policy** from a description and emits a synthetic test corpus
plus a report (each case's purpose, expected vs. actual validation):

```sh
PYTHONPATH=src .venv/bin/python -m candidatelab policy-audit --context account
PYTHONPATH=src .venv/bin/python -m candidatelab policy-audit --context wifi
```

---

## Output files

| File | Contents |
|------|----------|
| `*_wordlist.txt` | the candidate wordlist, one entry per line (deduplicated, length-filtered) |
| `*.session.json` | resumable checkpoint: gathered words/numbers/dates and the chosen knobs |
| `*_corpus.txt` / `*.report.json` | *(policy-audit mode)* synthetic test cases and their validation report |

Files containing gathered intelligence are written with restrictive `0600`
permissions on POSIX.

---

## Project layout

```
main.py                        single entrypoint (banner + menu)
opencode.json                  LM Studio runtime-model configuration
src/candidatelab/
  wordlistgen.py               target interview + wordlist generation (menu 3/4/6)
  policyaudit.py               defensive policy auditor (CLI) + model client/resolver
  logo.py                      PWNED banner
  engine.py                    bounded generation engine (expand/filter/dedupe)
  transforms.py                case / leet / affix variants
  combinations.py              word + number + date combinations
  dedupe.py                    exact SQLite-backed deduplication
  checkpoint.py                versioned checkpoints / resume
  presentation.py              retro Rich terminal styling
  config.py, planner.py, ...   shared configuration and planning
tests/                         focused offline tests + one live-model check
```

---

## Testing

```sh
# fast, fully offline suite (mocks the model)
PYTHONPATH=src .venv/bin/python -m pytest -q --ignore=tests/test_policyaudit_live.py

# optional live check — requires LM Studio running the model
PYTHONPATH=src .venv/bin/python -m pytest -q tests/test_policyaudit_live.py
```

The offline suite covers generation, deduplication, checkpoints/resume, the menu,
the model-config resolver and failure paths. The live check is skipped
automatically when the local endpoint is unreachable.

---

## Status

**Working:** local-model interview, predictive wordlist generation (account &
Wi-Fi), external wordlist merging, resume/checkpoints, auto-numbered outputs,
progress feedback, and the defensive policy-audit CLI.

**Not yet implemented:** menu items 1 (username+password), 2 (username only) and
5 (OSINT entity discovery); LLM-authored filters for merged wordlists (currently
deterministic length/charset filtering).

---

## Responsible use

PWNED is for **authorized security testing and education only** — password
audits of systems you own or have explicit permission to assess. It generates a
candidate list for a separate, authorized tool; it performs no network logins,
no cracking, and no automated profiling of people. Use it lawfully.
