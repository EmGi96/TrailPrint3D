"""User-defined OSM elements ("Custom" in the Extras group): the tag-rule
parser and its two outputs -- Overpass QL filters and a Python matcher.

Pure Python, no bpy: called from the picker server's HTTP thread
(/check_osm_tags) and from the Overpass worker threads.

Rule syntax -- one rule per line (or separated by ';'), a feature matches if
ANY rule matches; within a rule, ','-separated conditions must ALL match:

    natural=beach
    leisure=pitch, sport=tennis
    landuse=vineyard|orchard
    historic
    railway!=abandoned

    key            the tag exists
    key=v          equals          key=a|b   any of
    key!=v         differs (or missing)      key!=a|b  none of

Every rule needs at least one positive condition (exists / equals) -- a rule
of only negations would match the whole map. Keys and values are limited to
a safe character set, and every filter is quoted here, so user text can
never break out of the generated Overpass query.
"""

import hashlib
import re
from dataclasses import dataclass

MAX_SLOTS = 5
KINDS = tuple(f"CUSTOM{i + 1}" for i in range(MAX_SLOTS))
MAX_RULES = 10

_KEY_RE = re.compile(r"^[A-Za-z0-9_:.\-]+$")
# Letters (any script), digits, space and a few punctuation marks seen in
# real tag values. No quotes, backslashes, brackets or regex operators.
_VALUE_RE = re.compile(r"^[\w .:\-/&']+$")
_COND_RE = re.compile(r"^\s*([^=!~]+?)\s*(?:(!=|=)\s*(.*?))?\s*$")

# A rule that only checks one of these keys exists ("building", "highway")
# pulls a huge share of all OSM data -- allowed, but warned about.
_BROAD_KEYS = frozenset(
    {
        "building",
        "highway",
        "landuse",
        "natural",
        "amenity",
        "leisure",
        "waterway",
        "barrier",
        "power",
        "man_made",
        "shop",
        "surface",
    }
)


@dataclass(frozen=True)
class Condition:
    key: str
    op: str  # "exists", "=" or "!="
    values: tuple = ()


@dataclass(frozen=True)
class ParsedRules:
    rules: tuple = ()  # tuple of rules, each a tuple of Conditions
    error: str = ""
    warning: str = ""

    @property
    def ok(self):
        return bool(self.rules) and not self.error


def parse_rules(text):
    """Parse the tag-rule text. Never raises: problems come back in .error."""
    lines = [ln.strip() for ln in re.split(r"[\n;]", text or "")]
    lines = [ln for ln in lines if ln and not ln.startswith("#")]
    if not lines:
        return ParsedRules(error="Enter at least one tag, e.g. natural=beach")
    if len(lines) > MAX_RULES:
        return ParsedRules(error=f"At most {MAX_RULES} rules")
    rules = []
    broad = []
    for n, line in enumerate(lines, start=1):
        conds = []
        for part in line.split(","):
            part = part.strip()
            if not part:
                continue
            m = _COND_RE.match(part)
            if not m:
                return ParsedRules(error=f"Rule {n}: can't read '{part}'")
            key, op, raw = m.group(1).strip(), m.group(2), m.group(3)
            if not _KEY_RE.match(key):
                return ParsedRules(error=f"Rule {n}: invalid key '{key}'")
            if op is None:
                conds.append(Condition(key, "exists"))
                continue
            values = tuple(v.strip() for v in (raw or "").split("|"))
            if not values or any(not v for v in values):
                return ParsedRules(error=f"Rule {n}: '{key}{op}' needs a value")
            bad = next((v for v in values if not _VALUE_RE.match(v)), None)
            if bad is not None:
                return ParsedRules(error=f"Rule {n}: invalid value '{bad}'")
            conds.append(Condition(key, op, values))
        if not conds:
            continue
        if not any(c.op != "!=" for c in conds):
            return ParsedRules(
                error=f"Rule {n}: needs at least one tag that must exist or match"
            )
        positive = [c for c in conds if c.op != "!="]
        if all(c.op == "exists" and c.key in _BROAD_KEYS for c in positive):
            broad.append(positive[0].key)
        rules.append(tuple(conds))
    if not rules:
        return ParsedRules(error="Enter at least one tag, e.g. natural=beach")
    warning = ""
    if broad:
        key = broad[0]
        warning = (
            f"'{key}' on its own matches a lot of OSM data, so fetching can be "
            f"slow -- add a value, e.g. {key}=..."
        )
    return ParsedRules(rules=tuple(rules), warning=warning)


def _regex_alt(values):
    # '.' is the only regex metacharacter _VALUE_RE lets through.
    return "^(" + "|".join(v.replace(".", "[.]") for v in values) + ")$"


def _cond_ql(c):
    if c.op == "exists":
        return f'["{c.key}"]'
    if len(c.values) == 1:
        return f'["{c.key}"{c.op}"{c.values[0]}"]'
    op = "~" if c.op == "=" else "!~"
    return f'["{c.key}"{op}"{_regex_alt(c.values)}"]'


def overpass_filters(rules, geometry):
    """Overpass filter statements (no trailing ';') for parsed *rules*.
    LINE fetches ways only; AREA ways and multipolygon relations."""
    out = []
    for rule in rules:
        conds = "".join(_cond_ql(c) for c in rule)
        out.append(f"way{conds}")
        if geometry == "AREA":
            out.append(f'relation{conds}["type"="multipolygon"]')
    return out


def _cond_matches(c, tags):
    value = tags.get(c.key)
    if c.op == "exists":
        return value is not None
    if c.op == "=":
        return value in c.values
    return value not in c.values  # "!=" -- a missing tag differs too


def matches(rules, tags):
    return any(all(_cond_matches(c, tags) for c in rule) for rule in rules)


def signature(rules, geometry):
    """Short stable hash of a slot's query -- part of its Overpass cache key,
    so editing the tags never reuses tiles fetched for the old ones."""
    text = geometry + "|" + ";".join(
        ",".join(f"{c.key}{c.op}{'|'.join(c.values)}" for c in rule) for rule in rules
    )
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]
