"""Versioned shared configuration. Relative paths resolve beside the JSON file."""
from dataclasses import asdict, dataclass, fields, field
from .transforms import TransformSettings
from .combinations import CombinationSettings
from .profiles import Profile
from .mutations import FAMILIES
import math
import csv
import re
import json
from pathlib import Path


def parse_wordlist_paths(value: str) -> tuple[str, ...]:
    """CSV paths; double quotes allow filenames containing commas."""
    if not value.strip():
        return ()
    try:
        paths = next(csv.reader([value], skipinitialspace=True, strict=True))
    except csv.Error as exc:
        raise ValueError("invalid wordlist list; separate paths with commas and quote paths containing commas") from exc
    if any(not path.strip() for path in paths):
        raise ValueError("wordlist paths cannot be empty between commas")
    return tuple(path.strip() for path in paths)


def language_codes(values) -> tuple[str, ...]:
    if not isinstance(values, (tuple, list)) or not 1 <= len(values) <= 16:
        raise ValueError("languages must contain 1 to 16 language codes")
    if any(not isinstance(code, str) or not re.fullmatch(r"[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*", code) for code in values):
        raise ValueError("use language codes such as en, pt, es, fr, de or pt-PT")
    return tuple(dict.fromkeys(code.lower() for code in values))


@dataclass(frozen=True)
class Config:
    version: int = 1
    input: str | None = None
    inputs: tuple[str, ...] = ()
    output: str = "candidates.txt"
    seeds: tuple[str, ...] = ()
    mode: str = "passwords"
    preset: str = "generic"
    profiles: tuple[Profile, ...] = ()
    locale: str = "en"
    languages: tuple[str, ...] = ()
    encoding: str = "utf-8"
    output_format: str = "text"
    min_length: int | None = 1
    max_length: int | None = 40
    max_candidates: int = 10000
    max_attempts: int = 1000000
    max_output_bytes: int = 100000000
    transforms: TransformSettings = field(default_factory=TransformSettings)
    priorities: tuple[str, ...] = ()
    family_quotas: dict[str, int] = field(default_factory=dict)
    source_scheduling: str = "ordered"
    max_seconds: float | None = None
    max_input_records: int = 1000000
    provenance: bool = False
    combinations: CombinationSettings = field(default_factory=CombinationSettings)

    def __post_init__(self):
        for name in ("version", "max_candidates", "max_attempts", "max_output_bytes", "max_input_records"):
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise ValueError(f"{name} must be a nonnegative integer")
        if self.version != 1:
            raise ValueError("unsupported configuration version")
        for name in ("min_length", "max_length"):
            value = getattr(self, name)
            if value is not None and (type(value) is not int or not 0 <= value <= 4096):
                raise ValueError(f"{name} must be null or an integer between 0 and 4096")
        if self.min_length is not None and self.max_length is not None and self.min_length > self.max_length:
            raise ValueError("min_length exceeds max_length")
        for name in ("output", "mode", "preset", "locale", "encoding", "output_format"):
            if not isinstance(getattr(self, name), str) or not getattr(self, name):
                raise ValueError(f"{name} must be a nonempty string")
        if self.input is not None and (not isinstance(self.input, str) or not self.input):
            raise ValueError("input must be null or a nonempty path string")
        if not isinstance(self.inputs, (tuple, list)) or len(self.inputs) + bool(self.input) > 32:
            raise ValueError("supply at most 32 input wordlist paths")
        if any(not isinstance(path, str) or not path.strip() or "\n" in path or "\r" in path for path in self.inputs):
            raise ValueError("inputs must be a list of nonempty single-line path strings")
        object.__setattr__(self, "inputs", tuple(self.inputs))
        if not isinstance(self.languages, (tuple, list)):
            raise ValueError("languages must be a list of language codes")
        codes = language_codes(self.languages or (self.locale,))
        object.__setattr__(self, "languages", codes)
        object.__setattr__(self, "locale", codes[0])
        if self.preset not in ("generic", "wpa2"):
            raise ValueError("preset must be generic or wpa2")
        if self.preset == "wpa2" and ((self.max_length is not None and self.max_length < 8) or (self.min_length is not None and self.min_length > 63)):
            raise ValueError("WPA2 bounds must overlap its 8–63 character passphrase range")
        if self.mode not in ("passwords", "usernames", "pairs") or self.encoding != "utf-8" or self.output_format not in ("text", "jsonl"):
            raise ValueError("supported modes: passwords, usernames, pairs; formats: text, jsonl; encoding: utf-8")
        if self.mode == "pairs" and self.output_format != "jsonl":
            raise ValueError("pairs require output_format=jsonl for unambiguous username/password records")
        if self.mode == "usernames" and self.preset != "generic":
            raise ValueError("username mode requires the generic preset")
        if not isinstance(self.profiles, (list, tuple)) or len(self.profiles) > 16:
            raise ValueError("profiles must contain at most 16 supplied profiles")
        profiles = tuple(Profile.from_dict(value) if isinstance(value, dict) else value for value in self.profiles)
        if any(not isinstance(value, Profile) for value in profiles):
            raise ValueError("profiles must contain profile objects")
        if len({value.id for value in profiles}) != len(profiles):
            raise ValueError("profile ids must be unique")
        object.__setattr__(self, "profiles", profiles)
        if self.mode in ("pairs", "usernames") and (not profiles or any(not value.resolved_usernames for value in profiles)):
            raise ValueError("username and pair modes require profiles with supplied usernames or name templates")
        for name in ("seeds", "priorities"):
            value = getattr(self, name)
            if not isinstance(value, (tuple, list)) or any(not isinstance(x, str) for x in value):
                raise ValueError(f"{name} must be a list of strings")
            object.__setattr__(self, name, tuple(value))
        if isinstance(self.transforms, dict):
            object.__setattr__(self, "transforms", TransformSettings.from_dict(self.transforms))
        if not isinstance(self.transforms, TransformSettings):
            raise ValueError("transforms must be an object")
        if isinstance(self.combinations, dict):
            object.__setattr__(self, "combinations", CombinationSettings.from_dict(self.combinations))
        if not isinstance(self.combinations, CombinationSettings):
            raise ValueError("combinations must be an object")
        date_count = len(self.combinations.dates) * len(self.combinations.date_formats)
        if len(self.seeds) + len(self.combinations.numeric_tokens) + date_count > 256:
            raise ValueError("combined context catalog exceeds 256 tokens")
        if len(self.priorities) != len(set(self.priorities)) or any(x not in FAMILIES for x in self.priorities):
            raise ValueError("priorities must contain unique mutation families: original, normalization, case, leet, affix")
        if not isinstance(self.family_quotas, dict) or any(k not in FAMILIES + ("username",) or type(v) is not int or v < 0 for k, v in self.family_quotas.items()):
            raise ValueError("family_quotas must map known mutation families to nonnegative integers")
        if self.source_scheduling not in ("ordered", "round_robin"):
            raise ValueError("source_scheduling must be ordered or round_robin")
        if self.max_seconds is not None and (type(self.max_seconds) not in (int, float) or not math.isfinite(self.max_seconds) or self.max_seconds < 0):
            raise ValueError("max_seconds must be null or a finite nonnegative number")
        if type(self.provenance) is not bool or (self.provenance and self.output_format != "jsonl"):
            raise ValueError("provenance requires a boolean and output_format=jsonl")
        if len(self.seeds) > 256:
            raise ValueError("at most 256 supplied seed terms")
        for seed in self.seeds:
            if not seed or len(seed.encode("utf-8")) > 4096 or "\n" in seed or "\r" in seed:
                raise ValueError("seeds must be nonempty single-line UTF-8 terms, at most 4096 bytes")
        for profile in self.profiles:
            if len(self.seeds) + len(profile.seeds) + len(self.combinations.numeric_tokens) + date_count > 256:
                raise ValueError("combined profile context exceeds 256 tokens")
        if self.mode == "usernames":
            if self.source_paths or self.seeds or any(value.seeds or value.inputs for value in self.profiles):
                raise ValueError("username mode uses supplied usernames/name templates, not password seed lists")
            if self.transforms != TransformSettings() or self.combinations.enabled:
                raise ValueError("password transforms/combinations do not apply to username mode")
        elif self.profiles:
            if any(not (self.source_paths or self.seeds or value.inputs or value.seeds) for value in self.profiles):
                raise ValueError("each password profile needs supplied seeds or wordlists")
        elif not self.source_paths and not self.seeds:
            raise ValueError("supply at least one input wordlist or seed word")

    @property
    def source_paths(self) -> tuple[str, ...]:
        """Legacy input first, then inputs; repeated identical paths are read once."""
        return tuple(dict.fromkeys(((self.input,) if self.input else ()) + self.inputs))

    @classmethod
    def from_dict(cls, data):
        if not isinstance(data, dict):
            raise ValueError("configuration must be a JSON object")
        unknown = set(data) - {field.name for field in fields(cls)}
        if unknown:
            raise ValueError(f"unknown configuration fields: {', '.join(sorted(unknown))}")
        return cls(**data)

    @classmethod
    def load(cls, path):
        path = Path(path)
        with path.open("rb") as source:
            raw = source.read(262145)
        if len(raw) > 262144:
            raise ValueError("configuration exceeds 256 KiB")
        def unique_keys(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError(f"duplicate configuration field: {key}")
                result[key] = value
            return result
        data = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_keys)
        config = cls.from_dict(data)
        values = asdict(config)
        for key in ("input", "output"):
            if values[key] is not None:
                values[key] = str((path.parent / values[key]).resolve())
        values["inputs"] = [str((path.parent / Path(value).expanduser()).resolve()) for value in config.inputs]
        for profile in values["profiles"]:
            profile["inputs"] = [str((path.parent / Path(value).expanduser()).resolve()) for value in profile["inputs"]]
        return cls.from_dict(values)

    def save(self, path):
        """Write portable JSON without overwriting an existing configuration."""
        with Path(path).open("x", encoding="utf-8") as dest:
            json.dump(asdict(self), dest, ensure_ascii=False, indent=2)
            dest.write("\n")
