"""Bounded mutation families with deterministic ordering and provenance."""
from dataclasses import dataclass
from itertools import combinations, islice, product
import unicodedata

FAMILIES = ("original", "normalization", "case", "leet", "affix")


@dataclass(frozen=True)
class Mutation:
    value: str
    family: str
    edit_cost: int


def family_order(priorities):
    """The original is always first; selected families precede unlisted families."""
    return ("original",) + tuple(x for x in priorities if x != "original") + tuple(x for x in FAMILIES[1:] if x not in priorities)


def mutations(seed, settings, priorities=()):
    original = Mutation(seed, "original", 0)
    normalized = [Mutation(unicodedata.normalize(form, seed), "normalization", 1) for form in settings.normalizations]
    cases = [Mutation({"lower": str.lower, "upper": str.upper, "capitalized": str.capitalize}[op](seed), "case", 1)
             for op in settings.case]
    bases = [original] + normalized + cases

    def leet():
        for base in bases:
            positions = [i for i, char in enumerate(base.value) if char in settings.leet]
            for depth in range(1, min(settings.max_replaced, len(positions)) + 1):
                for indices in combinations(positions, depth):
                    for replacements in product(*(settings.leet[base.value[i]] for i in indices)):
                        chars = list(base.value)
                        for index, replacement in zip(indices, replacements):
                            chars[index] = replacement
                        yield Mutation("".join(chars), "leet", base.edit_cost + depth)

    def basic():
        yield from bases
        yield from leet()

    def affixes():
        if not settings.prefixes and not settings.suffixes:
            return
        # Affix inputs are bounded even when the leet derivation space is enormous.
        for base in islice(basic(), settings.max_variants):
            for prefix in ("",) + settings.prefixes:
                for suffix in ("",) + settings.suffixes:
                    if prefix or suffix:
                        yield Mutation(prefix + base.value + suffix, "affix", base.edit_cost + bool(prefix) + bool(suffix))

    factories = {"original": lambda: iter((original,)), "normalization": lambda: iter(normalized),
                 "case": lambda: iter(cases), "leet": leet, "affix": affixes}
    emitted = 0
    for family in family_order(priorities):
        for mutation in factories[family]():
            yield mutation
            emitted += 1
            if emitted >= settings.max_variants:
                return
