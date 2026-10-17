"""Explicitly supplied profiles; username rules are separate from password rules."""
from dataclasses import dataclass
import re
import unicodedata


@dataclass(frozen=True)
class Profile:
    id: str
    usernames: tuple[str, ...] = ()
    first_name: str = ""
    last_name: str = ""
    templates: tuple[str, ...] = ()
    template_case: str = "lower"
    seeds: tuple[str, ...] = ()
    inputs: tuple[str, ...] = ()
    max_candidates: int = 1000

    def __post_init__(self):
        if not isinstance(self.id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", self.id):
            raise ValueError("profile id must contain 1–64 ASCII letters, digits, underscores or hyphens")
        if type(self.max_candidates) is not int or self.max_candidates < 0:
            raise ValueError("profile max_candidates must be a nonnegative integer")
        for name, limit in (("usernames",32),("templates",2),("seeds",256),("inputs",32)):
            values=getattr(self,name)
            if not isinstance(values,(list,tuple)) or len(values)>limit or any(not isinstance(v,str) or not v or "\n" in v or "\r" in v for v in values):
                raise ValueError(f"profile {name} requires at most {limit} nonempty single-line strings")
            object.__setattr__(self,name,tuple(values))
        for name in ("first_name","last_name"):
            value=getattr(self,name)
            if not isinstance(value,str) or len(value.encode("utf-8"))>256 or "\n" in value or "\r" in value:
                raise ValueError(f"profile {name} must be a single-line string of at most 256 UTF-8 bytes")
        if any(len(value.encode("utf-8"))>4096 for value in self.seeds):
            raise ValueError("profile seed exceeds 4096 UTF-8 bytes")
        if self.template_case not in ("lower","preserve"):
            raise ValueError("template_case must be lower or preserve")
        if any(t not in ("first.last","first-initial+last") for t in self.templates):
            raise ValueError("supported username templates: first.last, first-initial+last")
        if self.templates and (not self.first_name.strip() or not self.last_name.strip()):
            raise ValueError("username templates require first_name and last_name")
        if any(len(name.encode("utf-8"))>256 for name in self.resolved_usernames):
            raise ValueError("username exceeds 256 UTF-8 bytes")

    @property
    def resolved_usernames(self):
        """Manual handles preserved. Templates remove whitespace and normalize NFC."""
        names=list(self.usernames)
        first=unicodedata.normalize("NFC", "".join(self.first_name.split()))
        last=unicodedata.normalize("NFC", "".join(self.last_name.split()))
        for template in self.templates:
            name=first+"."+last if template=="first.last" else first[0]+last
            names.append(name.lower() if self.template_case=="lower" else name)
        return tuple(dict.fromkeys(names))

    @classmethod
    def from_dict(cls,data):
        if not isinstance(data,dict) or set(data)-set(cls.__dataclass_fields__):
            raise ValueError("invalid or unknown profile fields")
        if "id" not in data:
            raise ValueError("profile id is required")
        return cls(**data)


def username_collisions(profiles):
    owners={}
    for profile in profiles:
        for name in profile.resolved_usernames:
            owners.setdefault(name,[]).append(profile.id)
    return {name: ids for name,ids in owners.items() if len(ids)>1}
