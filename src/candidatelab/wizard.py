"""Small question flow sharing exactly the batch configuration schema."""
from dataclasses import asdict
import json
from pathlib import Path
import sys
from .config import Config, language_codes, parse_wordlist_paths
from .transforms import TransformSettings
from .combinations import CombinationSettings
from .profiles import Profile, username_collisions
from .planner import plan


def terminal_read(question):
    print(question, end=" ", file=sys.stderr, flush=True)
    return input()


def collect(read=terminal_read, write=None, *, context=None) -> Config:
    write = write or (lambda text: print(text, file=sys.stderr))
    write("CANDIDATELAB // OFFLINE AUDIT INPUTS")
    def number(question, default=None, optional=False):
        while True:
            answer = read(question).strip()
            if not answer:
                return None if optional else default
            try:
                value = int(answer)
                if value >= 0:
                    return value
            except ValueError:
                pass
            write("Enter a nonnegative integer, or leave blank for the displayed default.")

    def yes_no(question):
        while True:
            answer = read(question).strip().lower()
            if answer in ("y", "yes"):
                return True
            if answer in ("", "n", "no"):
                return False
            write("Please answer y or n. Press Enter for no.")


    write("1. Password candidates from supplied words")
    write("2. Wi-Fi passphrase candidates (WPA2-Personal preset)")
    write("3. Username candidates from supplied handles/name templates")
    write("4. Username/password candidate pairs")
    write("Reviewed Gemma interview context loaded; configure generation settings below." if context else "Manual input mode. Use init without --manual for the local Gemma interview; OSINT is not implemented.")
    choice = context["choice"] if context else (read("Choose [1]:").strip() or "1")
    while choice not in ("1", "2", "3", "4"):
        write("Choose 1, 2, 3 or 4.")
        choice = read("Choose [1]:").strip() or "1"
    preset = "wpa2" if choice == "2" else "generic"
    mode = {"3": "usernames", "4": "pairs"}.get(choice, "passwords")
    if preset == "wpa2":
        write("Wi-Fi preset: printable ASCII passphrases, 8–63 characters. Your 1–40 defaults narrow this to 8–40; customize the limits if needed.")
    write("Language examples: en (English), pt (Portuguese), es (Spanish), fr (French), de (German), it (Italian).")
    write("Use one or more comma-separated codes; regional codes such as en-GB and pt-PT also work.")
    if context and context.get("languages"):
        languages = language_codes(context["languages"])
        write("Using interview language context: " + ", ".join(languages))
    else:
        while True:
            try:
                answer = read("Languages [en; example: en,pt,es]:").strip() or "en"
                languages = language_codes(tuple(code.strip() for code in answer.split(",")))
                break
            except ValueError as exc:
                write(str(exc))
    write("Languages are saved as context; automatic dictionary/AI expansion is not available yet.")
    profiles = list(context.get("profiles", ())) if context else []
    if mode in ("usernames", "pairs") and not profiles:
        write("Profiles use only the usernames, names and starting words you supply. No account discovery is performed.")
        while True:
            while True:
                try:
                    handles = parse_wordlist_paths(read("Supplied usernames, comma-separated [blank for name templates]:"))
                    break
                except ValueError as exc:
                    write(str(exc))
            first = last = ""
            templates = ()
            if yes_no("Generate username variants from a supplied first/last name? [y/N]:"):
                first = read("Supplied first name:")
                last = read("Supplied last name:")
                templates = ("first.last", "first-initial+last")
                write("Name templates use lowercase NFC text with whitespace removed; supplied handles keep their exact spelling.")
            profile_seeds = []
            if mode == "pairs":
                write("Enter starting words/phrases for THIS profile only, one per line; blank finishes. Shared files/words are asked later.")
                while len(profile_seeds) < 256:
                    term = read("Profile starting word or phrase:")
                    if not term:
                        break
                    profile_seeds.append(term)
            cap = number("Maximum candidates for this profile [1000; 0 skips it]:", 1000)
            profile = Profile(id=f"profile-{len(profiles)+1}", usernames=handles, first_name=first,
                              last_name=last, templates=templates, seeds=tuple(profile_seeds), max_candidates=cap)
            if not profile.resolved_usernames:
                write("Supply at least one username or a first/last name with templates.")
                continue
            profiles.append(profile)
            if len(profiles) >= 16 or not yes_no("Add another supplied profile? [y/N]:"):
                break
        collisions = username_collisions(profiles)
        if collisions:
            write(f"Username collisions across profiles: {len(collisions)}. Review the profile configuration before generation.")
    if mode == "usernames":
        maximum_count = number("Maximum emitted candidates [10000; 0 emits nothing]:", 10000)
        attempts = number("Maximum attempted records [1000000]:", 1000000)
        byte_budget = number("Maximum output bytes [100000000]:", 100000000)
        destination = read("Username destination [usernames.txt]:").strip() or "usernames.txt"
        config = Config(mode=mode, profiles=tuple(profiles), languages=languages,
                        output=str(Path(destination).expanduser().resolve()), max_candidates=maximum_count,
                        max_attempts=attempts, max_output_bytes=byte_budget)
        write(json.dumps(asdict(config), ensure_ascii=False, indent=2))
        write(f"At most {min(maximum_count, sum(len(p.resolved_usernames) for p in profiles))} username candidates before deduplication and budgets; time unknown.")
        return config
    if mode == "pairs":
        write("The following wordlists and starting words are SHARED across the selected profiles. Profile-specific words remain isolated.")
    while True:
        try:
            filenames = parse_wordlist_paths(read("Input wordlist paths, comma-separated [blank to skip]:"))
            sources = tuple(str(Path(name).expanduser().resolve()) for name in filenames)
            if len(sources) > 32:
                raise ValueError("Supply at most 32 wordlist paths.")
            break
        except ValueError as exc:
            write(str(exc))
    if context is not None and "seeds" in context:
        seeds = list(context["seeds"])
        write(f"Using {len(seeds)} explicitly approved interview starting words/phrases; not asking for them again.")
    else:
        seeds = []
        write("Enter starting words or phrases you want the generator to combine and modify.")
        write("Examples: copper, moon, night owl. Enter ONE word or phrase per line, without commas.")
        write("Press Enter on an empty line when finished. Spaces inside each phrase are preserved.")
        while True:
            term = read("Starting word or phrase:")
            if not term:
                break
            seeds.append(term)
            if len(seeds) >= 256:
                write("256-term limit reached.")
                break

    minimum = number("Minimum password characters [1]:", 1)
    maximum = number("Maximum password characters [40]:", 40)
    while maximum < minimum or maximum > 4096 or minimum > 4096 or (preset == "wpa2" and (maximum < 8 or minimum > 63)):
        write("Use bounds between 0 and 4096, with minimum <= maximum. Wi-Fi bounds must overlap 8–63.")
        minimum = number("Minimum password characters [1]:", 1)
        maximum = number("Maximum password characters [40]:", 40)
    maximum_count = number("Maximum emitted candidates [10000; 0 emits nothing]:", 10000)
    attempts = number("Maximum attempted records [1000000]:", 1000000)
    byte_budget = number("Maximum output bytes [100000000]:", 100000000)
    use_case = yes_no("Add case variants (lowercase, UPPERCASE, Capitalized)? [y/N]:")
    case = ("lower", "upper", "capitalized") if use_case else ()
    use_leet = yes_no("Add bounded leet substitutions? [y/N]:")
    prefix = read("Optional literal prefix [blank: none]:")
    suffix = read("Optional literal suffix [blank: none]:")
    transforms = TransformSettings(case=case, prefixes=(prefix,) if prefix else (), suffixes=(suffix,) if suffix else (),
                                   leet={"a": ("@",), "e": ("3",), "i": ("1",), "o": ("0",), "s": ("5",), "t": ("7",)} if use_leet else {})
    use_combinations = yes_no("Combine starting words and phrases? [y/N]:")
    combination_settings = CombinationSettings()
    if use_combinations:
        depth = number("Maximum tokens per combination [2; supported: 2 or 3]:", 2)
        separator = read("Literal separator between terms [blank: none]:")
        cap = number("Maximum raw combinations [10000]:", 10000)
        combination_settings = CombinationSettings(enabled=True, max_tokens=depth, separators=(separator,), max_combinations=cap)
    default_destination = "candidates.jsonl" if mode == "pairs" else "candidates.txt"
    destination = read(f"Candidate destination [{default_destination}]:").strip() or default_destination
    config = Config(mode=mode, profiles=tuple(profiles), output_format="jsonl" if mode == "pairs" else "text", preset=preset, inputs=sources, seeds=tuple(seeds), output=str(Path(destination).expanduser().resolve()),
                    languages=languages, min_length=minimum, max_length=maximum,
                    max_candidates=maximum_count, max_attempts=attempts, max_output_bytes=byte_budget, transforms=transforms, combinations=combination_settings)
    write(json.dumps(asdict(config), ensure_ascii=False, indent=2))
    preview = plan(config)
    write(f"Emitted candidate upper bound: {preview['emitted_upper']}; raw expansion upper bound: {preview['raw_expansion_upper']} (null means unknown).")
    write(preview["estimate_notes"])
    return config


def initialize(path, read=terminal_read, write=None):
    write = write or (lambda text: print(text, file=sys.stderr))
    if Path(path).exists():
        raise FileExistsError(f"configuration already exists: {path}")
    config = collect(read, write)
    if read("Save this configuration? [y/N]:").strip().lower() not in ("y", "yes"):
        write("Configuration not saved.")
        return None
    config.save(path)
    write(f"Saved {path}. Run: candidatelab generate --config {path}")
    return config
