#!/usr/bin/env python3
"""Cross-paper material LABEL grouping, computed from `curves.material_name`.

What this module claims, and what it refuses to claim
----------------------------------------------------
It groups published curves whose material labels normalise to the same string.
That is a claim about LABEL AGREEMENT. It is not a claim that the materials are
the same, and nothing here should ever be worded as one: labels are reused and
redefined between research groups, and nobody has verified any of these pairs.

Provenance: `public.pxrd_curves.material_name` only. No third-party dataset is
read, copied, or derived from here. In particular the local `peter_match` /
`peter_cof` tables are NOT consulted. That is a deliberate design decision, and
it is the better one on the merits as well as on licensing:

  * `peter_match.peter_row` cannot express cross-paper identity at all. Matching
    in tools/merge_peter.py is DOI-gated and every Peter row carries exactly one
    DOI, so all 475 tier-A pilot groups span exactly one paper, by arithmetic.
  * `peter_match.matched_name`, its only cross-paper field, is a normalised copy
    of a third-party content column. Publishing it would publish that dataset.
  * Measured on the 1,000-paper pilot, `matched_name` yields 18 multi-paper
    groups, 17 of which `material_name` already finds on its own.

Why the gate exists
-------------------
The largest label groups are the least meaningful. In the pilot the biggest
"material" is the literal word "COF" (26 papers), and COF-1..COF-5 are per-paper
serial numbering: one paper alone supplies COF-1 through COF-5. Merging those
would assert that one paper's COF-2 IS another paper's COF-2, which is false.

So a group is only publishable when its key survives `classify_label`. Generic,
serial, and paper-local keys get no group id, no badge, and no page. This costs
real recall - some genuine catalogue names (COF-5, NKCOF-1) are refused because
no string test separates them from a paper-local tag - and that trade is taken
deliberately. A missing link is a disclosure; a fabricated one is a false claim.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Sequence

# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------
# Defensive: material_name is clean in the current database, but the upstream
# pipeline reads figure legends, and legend text has carried "<sub>" markup
# before. Stripping the tag while keeping its text is what preserves the digit:
# a normaliser that deletes everything outside [a-z0-9] turns "TpPa-NH<sub>2</sub>"
# into "tppanhsub2sub", which can never equal "tppanh2".
_MARKUP = re.compile(r"</?[a-z][^>]{0,40}>", re.IGNORECASE)
_ENTITIES = {"&amp;": "&", "&lt;": "<", "&gt;": ">", "&nbsp;": " ", "&quot;": '"'}

# Unicode sub/superscript forms carry composition: Fe3O4 and Fe₃O₄ are one
# material written two ways, and TpPa-SO3H / TpPa-SO₃H likewise.
_DIGIT_FORMS = {
    "₀": "0", "₁": "1", "₂": "2", "₃": "3", "₄": "4",
    "₅": "5", "₆": "6", "₇": "7", "₈": "8", "₉": "9",
    "⁰": "0", "¹": "1", "²": "2", "³": "3", "⁴": "4",
    "⁵": "5", "⁶": "6", "⁷": "7", "⁸": "8", "⁹": "9",
    "ₐ": "a", "ₑ": "e", "ₕ": "h", "ᵢ": "i", "ₙ": "n", "ₒ": "o",
    "ₚ": "p", "ₛ": "s", "ₜ": "t", "ⁿ": "n", "ᵃ": "a", "ᵇ": "b",
}
# Spelled out rather than folded to a latin letter: alpha-COF and a-COF are not
# the same string in any paper, and collapsing gamma to "g" would invent a
# collision with a real "g-" prefix.
_GREEK = {
    "α": "alpha", "β": "beta", "γ": "gamma", "δ": "delta", "ε": "epsilon",
    "ζ": "zeta", "η": "eta", "θ": "theta", "κ": "kappa", "λ": "lambda",
    "μ": "mu", "ν": "nu", "π": "pi", "ρ": "rho", "σ": "sigma", "τ": "tau",
    "φ": "phi", "χ": "chi", "ψ": "psi", "ω": "omega",
}
# Every dash variant a publisher's typesetter emits, plus the soft hyphen.
_DASHES = "-‐‑‒–—―−­⁃"

# `@` and `/` SURVIVE normalisation, and this is load-bearing. They are the
# composite operators: "COF@S" is a sulfur composite and "COFs" is a plural, so
# deleting `@` would merge them. Likewise "COF/MXene" vs "COF@MXene", and
# "M-COF@" vs "M-COF". Everything else outside [a-z0-9] is formatting noise.
_STRIP = re.compile(r"[^a-z0-9@/]")


def normalise_label(raw: object) -> str:
    """Fold one raw material label to its comparison key.

    Formatting is discarded; composition and composite structure are kept.
    Returns "" for anything with no alphanumeric content, which never groups.
    """
    if raw is None:
        return ""
    text = str(raw)
    for entity, char in _ENTITIES.items():
        text = text.replace(entity, char)
    text = _MARKUP.sub("", text).lower()
    out: list[str] = []
    for char in text:
        if char in _DIGIT_FORMS:
            out.append(_DIGIT_FORMS[char])
        elif char in _GREEK:
            out.append(_GREEK[char])
        elif char in _DASHES:
            out.append("-")
        else:
            out.append(char)
    return _STRIP.sub("", "".join(out))


# ---------------------------------------------------------------------------
# Specificity: which keys are allowed to link papers
# ---------------------------------------------------------------------------
# Bare framework-class words. On their own they name a field, not a material.
_FRAMEWORK_STEM = (
    r"(?:cofs?|mofs?|zifs?|pofs?|cmps?|ctfs?|pafs?|hofs?|cops?|imps?|"
    r"pims?|sbas?|zsms?)"
)
# Stems that are counters rather than names, so "<stem><n>" is enumeration.
_COUNTER_STEM = r"(?:sample|compound|complex|material|polymer|catalyst|product|entry|no)"

# A whole label that is only a class word, a process adjective, or a linkage
# chemistry. "Imine-COF" and "Amide COF" name a bond, not a compound: two
# papers' imine COFs are routinely different materials.
_GENERIC_EXACT = {
    "cof", "cofs", "mof", "mofs", "zif", "zifs", "pof", "pofs", "cmp", "cmps",
    "ctf", "ctfs", "paf", "pafs", "hof", "hofs", "cop", "cops",
    "framework", "frameworks", "polymer", "polymers", "resin",
    "sample", "samples", "specimen", "blank", "control", "reference",
    "standard", "pristine", "product", "material", "materials", "compound",
    "compounds", "complex", "powder", "solid", "crystal", "crystals",
    "bulk", "film", "films", "membrane", "membranes", "composite",
    "composites", "catalyst", "catalysts", "support", "precursor", "monomer",
    "ligand", "adsorbent", "asprepared", "assynthesized", "assynthesised",
    "asmade", "titlecomplex", "titlecompound", "thiswork", "ourcof",
    "experimental", "simulated", "calculated", "observed",
}
# Linkage / process adjectives that qualify a class word without naming a
# compound. "<adjective><framework>" is refused for the same reason as above.
_CLASS_ADJECTIVE = (
    r"(?:imine|imino|amide|amido|amine|amino|azine|azo|hydrazone|boronate|"
    r"boroxine|borate|ketoenamine|enamine|olefin|vinylene|triazine|"
    r"covalent|crystalline|amorphous|porous|pure|raw|neat|blank|parent|"
    r"pristine|bare|fresh|used|spent|recovered|recycled|regenerated|"
    r"activated|calcined|hydrated|dried|wet|dry|thin|thick|nano|micro|meso|"
    r"macro|hollow|solid|magnetic|modified|functionalized|functionalised|"
    r"doped|loaded|supported|ref|reference|control|standard|new|old|"
    r"synthesized|synthesised|prepared|obtained|target|initial|final)"
)
_PROCESS_SUFFIX = r"(?:film|films|membrane|membranes|powder|composite|bulk|sample|nanosheets?|nanoparticles?|ns|np|nps)"

# 1. A class word carrying only an adjective or a process suffix.
_GENERIC_PATTERN = re.compile(
    r"^(?:" + _CLASS_ADJECTIVE + r")?" + _FRAMEWORK_STEM + r"(?:" + _PROCESS_SUFFIX + r")?$"
)
# 2. Enumeration: a class or counter stem plus a short local tag. The tag may be
#    a number (COF-1, compound 2) or a bare letter token (COF-A, COF-H, COF-Br,
#    COF-OMe) - papers assign both the same way, so both are refused. Two
#    exemptions survive, because both are the literature's catalogue form rather
#    than a local tag:
#      * three or more digits  - COF-300, COF-366
#      * two or three letters followed by a number - COF-LZU1, COF-NEU1
#    A single letter before the number is NOT exempt: COFa-2 is a variant tag.
_SERIAL_PATTERN = re.compile(
    r"^(?:" + _FRAMEWORK_STEM + r"|" + _COUNTER_STEM + r")"
    r"(?:\d{1,2}[a-z]?|[a-z]\d{1,2}|[a-z]{1,3})$"
)
# 3. A short prefix bolted onto a class word, with an optional small serial:
#    M-COF, H-COF, P-COF, Bpy-COF, TT-COF, eCOF-2, H-COF1. These are how a paper
#    names its own variants. The rule also refuses some genuine institutional
#    catalogue names (NKCOF-1, TTI-COF); no string test separates the two cases,
#    and refusing is the safe direction.
_PAPER_LOCAL_PATTERN = re.compile(
    r"^[a-z]{1,3}\d{0,2}" + _FRAMEWORK_STEM + r"(?:[a-z]?\d{1,2})?$"
)
# A key too short to identify anything: "1", "4", "1a", "AB", "MS", "CS".
_MIN_KEY_LENGTH = 3

SPECIFICITY_VALUES = ("specific", "paper_local", "serial", "generic")


def classify_label(key: str) -> str:
    """Classify a normalised key. Only 'specific' may ever link papers.

    generic     names a class or a process, not a compound  ("COF", "Imine-COF")
    serial      per-paper enumeration                       ("COF-1", "COF-A")
    paper_local a paper's own variant tag                   ("M-COF", "H-COF1")
    specific    everything else                             ("TpPa-1", "COF-300")
    """
    if not key or len(key) < _MIN_KEY_LENGTH:
        return "paper_local"
    if key in _GENERIC_EXACT or _GENERIC_PATTERN.match(key):
        return "generic"
    if _SERIAL_PATTERN.match(key):
        return "serial"
    if _PAPER_LOCAL_PATTERN.match(key):
        return "paper_local"
    return "specific"


# ---------------------------------------------------------------------------
# Grouping
# ---------------------------------------------------------------------------
@dataclass
class LabelGroup:
    group_key: str
    display_name: str
    label_variants: list[str]
    papers: set[str] = field(default_factory=set)
    curve_count: int = 0
    match_basis: str = "label_identical"
    specificity: str = "specific"

    @property
    def paper_count(self) -> int:
        return len(self.papers)

    def publishable(self) -> bool:
        """A group only links papers when it is specific AND spans more than one.

        A single-paper group is not linkage; it is one paper's own label. It is
        never given an id, so an absent badge means "not linked" rather than
        "unique material".
        """
        return self.specificity == "specific" and self.paper_count > 1


def build_groups(
    rows: Iterable[Sequence[object]],
) -> tuple[dict[str, LabelGroup], dict[str, str]]:
    """Group (series_id, paper_id, material_name) rows by normalised label.

    Returns (groups keyed by group_key, series_id -> group_key for publishable
    groups only). Curves with a blank label, or a label whose group is not
    publishable, are absent from the assignment map and carry a NULL group id.
    """
    groups: dict[str, LabelGroup] = {}
    raw_counts: dict[str, dict[str, int]] = {}
    members: dict[str, list[str]] = {}

    for series_id, paper_id, raw in rows:
        key = normalise_label(raw)
        if not key:
            continue
        label = str(raw).strip()
        group = groups.get(key)
        if group is None:
            group = groups[key] = LabelGroup(
                group_key=key, display_name=label, label_variants=[]
            )
            raw_counts[key] = {}
            members[key] = []
        group.papers.add(str(paper_id))
        group.curve_count += 1
        raw_counts[key][label] = raw_counts[key].get(label, 0) + 1
        members[key].append(str(series_id))

    assignment: dict[str, str] = {}
    for key, group in groups.items():
        counts = raw_counts[key]
        # Most frequent raw label wins; ties break on the shortest, then
        # alphabetically, so the display name is stable across imports.
        group.display_name = min(counts, key=lambda name: (-counts[name], len(name), name))
        group.label_variants = sorted(counts)
        group.match_basis = (
            "label_identical" if len(counts) == 1 else "label_normalised"
        )
        group.specificity = classify_label(key)
        if group.publishable():
            for series_id in members[key]:
                assignment[series_id] = key
    return groups, assignment
