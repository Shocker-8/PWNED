"""Deterministic lazy mutation families with explicit per-seed work caps."""
from dataclasses import dataclass, field


@dataclass(frozen=True)
class TransformSettings:
    case: tuple[str, ...] = ()
    prefixes: tuple[str, ...] = ()
    suffixes: tuple[str, ...] = ()
    leet: dict[str, tuple[str, ...]] = field(default_factory=dict)
    normalizations: tuple[str, ...] = ()
    max_replaced: int = 1
    max_variants: int = 64

    def __post_init__(self):
        if type(self.max_replaced) is not int or not 0 <= self.max_replaced <= 3:
            raise ValueError("max_replaced must be an integer from 0 to 3")
        if type(self.max_variants) is not int or not 1 <= self.max_variants <= 4096:
            raise ValueError("max_variants must be an integer from 1 to 4096")
        for name in ("case", "prefixes", "suffixes", "normalizations"):
            value = getattr(self, name)
            if not isinstance(value, (list, tuple)) or len(value) > 32 or any(not isinstance(x, str) for x in value):
                raise ValueError(f"{name} must contain at most 32 strings")
            object.__setattr__(self, name, tuple(value))
        if any(x not in ("NFC", "NFD", "NFKC", "NFKD") for x in self.normalizations):
            raise ValueError("unsupported Unicode normalization")
        if any(x not in ("lower", "upper", "capitalized") for x in self.case):
            raise ValueError("case supports lower, upper and capitalized")
        if any(len(x.encode("utf-8")) > 256 or "\n" in x or "\r" in x for x in self.prefixes + self.suffixes):
            raise ValueError("affixes must be single-line strings of at most 256 bytes")
        if not isinstance(self.leet, dict) or len(self.leet) > 32:
            raise ValueError("leet must be a map of at most 32 characters")
        normalized = {}
        for key, choices in self.leet.items():
            if not isinstance(key, str) or len(key) != 1 or key in "\r\n":
                raise ValueError("leet keys must be single non-newline characters")
            if not isinstance(choices, (tuple, list)) or not 1 <= len(choices) <= 4:
                raise ValueError("leet choices must contain 1 to 4 replacement characters")
            if any(not isinstance(x, str) or len(x) != 1 or x in "\r\n" for x in choices):
                raise ValueError("leet values must be single non-newline characters")
            normalized[key] = tuple(choices)
        object.__setattr__(self, "leet", normalized)

    @classmethod
    def from_dict(cls, data):
        if not isinstance(data, dict):
            raise ValueError("transforms must be an object")
        unknown = set(data) - set(cls.__dataclass_fields__)
        if unknown:
            raise ValueError("unknown transform fields: " + ", ".join(sorted(unknown)))
        return cls(**data)


def variants(seed: str, settings: TransformSettings):
    """Original, normalization, case, leet, affixes; cap counts raw variants.

    Capitalized uses str.capitalize. Leet keys match exact characters.
    Affixes apply once; no recursive mutations. See mutations.py for metadata.
    """
    from .mutations import mutations
    for mutation in mutations(seed, settings):
        yield mutation.value
