"""Bounded combinations of explicitly supplied terms; no implicit calendar values."""
from dataclasses import dataclass
from datetime import date
from itertools import permutations, product


@dataclass(frozen=True)
class CombinationSettings:
    enabled: bool = False
    max_tokens: int = 2
    max_combinations: int = 10000
    separators: tuple[str, ...] = ("",)
    allow_reuse: bool = False
    numeric_tokens: tuple[str, ...] = ()
    dates: tuple[str, ...] = ()
    date_formats: tuple[str, ...] = ()

    def __post_init__(self):
        if type(self.enabled) is not bool or type(self.allow_reuse) is not bool:
            raise ValueError("combination enabled/allow_reuse must be booleans")
        if type(self.max_tokens) is not int or self.max_tokens not in (2, 3):
            raise ValueError("max_tokens must be 2 or 3")
        if type(self.max_combinations) is not int or not 0 <= self.max_combinations <= 1000000:
            raise ValueError("max_combinations must be between 0 and 1000000")
        for key in ("separators", "numeric_tokens", "dates", "date_formats"):
            values = getattr(self, key)
            if not isinstance(values, (list, tuple)) or len(values) > 32 or any(not isinstance(v, str) for v in values):
                raise ValueError(f"{key} must be a list of at most 32 strings")
            object.__setattr__(self, key, tuple(values))
        if not self.separators or any(len(x.encode()) > 32 or "\n" in x or "\r" in x for x in self.separators):
            raise ValueError("supply 1–32 single-line separators of at most 32 bytes")
        if any(not x.isascii() or not x.isdigit() or len(x) > 32 for x in self.numeric_tokens):
            raise ValueError("numeric_tokens require 1–32 ASCII digits per token")
        allowed = {"%Y", "%y", "%Y%m%d", "%d%m%Y", "%m%d%Y", "%d%m", "%m%d"}
        if any(fmt not in allowed for fmt in self.date_formats):
            raise ValueError("unsupported date format; use an explicit supported numeric format")
        if self.dates and not self.date_formats:
            raise ValueError("dates require explicit date_formats")
        for value in self.dates:
            if len(value) != 10:
                raise ValueError("dates require YYYY-MM-DD")
            date.fromisoformat(value)

    @classmethod
    def from_dict(cls, data):
        if not isinstance(data, dict) or set(data) - set(cls.__dataclass_fields__):
            raise ValueError("invalid or unknown combination settings")
        return cls(**data)


def combinations(seeds, settings: CombinationSettings):
    """Yield concatenations only, in depth/index/separator order; cap raw attempts."""
    if not settings.enabled or settings.max_combinations == 0:
        return
    catalog = list(seeds) + list(settings.numeric_tokens)
    for value in settings.dates:
        catalog.extend(date.fromisoformat(value).strftime(fmt) for fmt in settings.date_formats)
    catalog = list(dict.fromkeys(catalog))
    if len(catalog) > 256:
        raise ValueError("combined context catalog exceeds 256 tokens")
    emitted = 0
    for depth in range(2, settings.max_tokens + 1):
        indices = product(range(len(catalog)), repeat=depth) if settings.allow_reuse else permutations(range(len(catalog)), depth)
        for group in indices:
            for separator in settings.separators:
                yield separator.join(catalog[i] for i in group)
                emitted += 1
                if emitted >= settings.max_combinations:
                    return
