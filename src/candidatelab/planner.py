"""Non-enumerating bounds for the implemented generator; never invokes a model."""
from dataclasses import asdict
from datetime import date
from math import perm
from .profiles import username_collisions
from .mutations import family_order


def _combination_bound(seeds, settings):
    if not settings.enabled:
        return 0
    tokens = list(seeds) + list(settings.numeric_tokens)
    for value in settings.dates:
        tokens.extend(date.fromisoformat(value).strftime(fmt) for fmt in settings.date_formats)
    count = len(set(tokens))
    total = sum(count**depth if settings.allow_reuse else perm(count, depth)
                for depth in range(2, settings.max_tokens + 1))
    return min(settings.max_combinations, total * len(settings.separators))


def plan(config):
    settings = config.transforms
    variant_cap = settings.max_variants
    if not settings.leet and not settings.prefixes and not settings.suffixes:
        variant_cap = min(variant_cap, 1 + len(settings.case) + len(settings.normalizations))
    profiles = config.profiles or (None,)
    rows = []
    for profile in profiles:
        seeds = config.seeds + (profile.seeds if profile else ())
        paths = tuple(dict.fromkeys(config.source_paths + (profile.inputs if profile else ())))
        names = profile.resolved_usernames if profile else ()
        cap = profile.max_candidates if profile else config.max_candidates
        if config.mode == "usernames":
            upper = len(names)
        elif paths:
            upper = None
        else:
            upper = (len(seeds) + _combination_bound(seeds, config.combinations)) * variant_cap
            if config.mode == "pairs":
                upper *= len(names)
        if cap == 0:
            upper = 0
        rows.append({"profile_id": profile.id if profile else "default", "source_count": len(paths),
                     "supplied_seed_count": len(seeds), "username_count": len(names),
                     "raw_expansion_upper": upper, "candidate_cap": cap})
    upper = None if any(row["raw_expansion_upper"] is None for row in rows) else sum(row["raw_expansion_upper"] for row in rows)
    emitted = min(config.max_candidates, config.max_attempts, config.max_output_bytes // 2,
                  sum(row["candidate_cap"] for row in rows))
    if upper is not None:
        emitted = min(emitted, upper)
    minimum, maximum = config.min_length, config.max_length
    if config.preset == "wpa2":
        minimum = max(8, minimum or 0)
        maximum = min(63, maximum if maximum is not None else 63)
    return {"mode": config.mode, "preset": config.preset, "output_format": config.output_format,
            "output": config.output, "raw_expansion_upper": upper, "emitted_upper": emitted,
            "exact_unique_count": None, "estimated_seconds": None,
            "estimate_notes": "Upper bounds, not expected counts. Filtering/deduplication may reduce output. Wordlists are not scanned; no measured throughput/ETA is available.",
            "effective_password_lengths": None if config.mode == "usernames" else {"minimum": minimum, "maximum": maximum},
            "budgets": {"attempts": config.max_attempts,"candidates": config.max_candidates,"bytes": config.max_output_bytes, "input_records": config.max_input_records, "seconds": config.max_seconds},
            "order": f"Round-robin profiles; {config.source_scheduling} sources. Per-seed mutation tiers: {', '.join(family_order(config.priorities))}. Source rank preserves input order within each stream.",
            "custom_priority_tiers": list(family_order(config.priorities)), "profiles": rows,
            "family_quotas": config.family_quotas,
            "quota_notes": "Global admitted raw attempts per family. Quota-skipped records still consume max_attempts; duplicate/filter checks occur after admission. Priorities are per seed, not global probability order.",
            "provenance": config.provenance,
            "time_notes": "Cooperative checks during generation, after source preflight; blocked I/O cannot be preempted. Time-bounded output can differ between runs.",
            "username_collisions": username_collisions(config.profiles)}
