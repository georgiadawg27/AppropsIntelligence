"""
Record IDs: one allocator for every record type and every subcommittee.

    <TYPE>-<CODE>-####     any subcommittee (OBS-LHHS-0001, CMP-LHHS-1372, a new one: OBS-LEG-0001)
    <TYPE>-####            CJS only: the first subcommittee's IDs carry no code (OBS-0001, CMP-0079), and a new CJS
                           ID keeps that pattern (BARE). Never renumbered.

TYPE is the record's prefix (OBS, VAL, CA, REL, HN, CMP, AAT; TYPES). The number is the next one after the highest
ever used in that (TYPE, CODE) namespace -- retired IDs count, so an ID is never reused. Widths are the ones each
namespace already uses (WIDTH); a number past the width just gets longer.

Derived IDs are not allocated here: VAL-TXT-<observation suffix> (check_legislative_text), VAL-MR-/VAL-MRV-/
VAL-MRE-<suffix> and VAL-MRC-<CAUSE> (measure_reconcile) take the observation's own suffix, so they are unique when
observation IDs are. Their qualifiers (RESERVED) can never be a subcommittee code, and a code is upper-case letters
and digits starting with a letter (CODE), so no two subcommittees' namespaces can hold the same ID
(tests/test_any_subcommittee.py: IdNamespaces).
"""

import re

TYPES = ("OBS", "VAL", "CA", "REL", "HN", "CMP", "AAT")
BARE = "CJS"                                    # the subcommittee whose IDs carry no code
CODE = re.compile(r"[A-Z][A-Z0-9]{1,9}")
# qualifiers of derived IDs (VAL-TXT-..., VAL-MR-...) and the record types themselves: never a subcommittee code
RESERVED = {"TXT", "MR", "MRV", "MRC", "MRE"} | set(TYPES)
# (TYPE, CODE) -> digits, where a namespace uses other than 4 (VAL-LHHS-#####; CJS's VAL-#### keeps 4)
WIDTH = {("VAL", BARE): 4}
DEFAULT_WIDTH = {"VAL": 5}


class IdError(ValueError):
    pass


def check_code(code):
    """A subcommittee code usable in IDs, or IdError saying why not."""
    if code == BARE:
        return code
    if not CODE.fullmatch(code or ""):
        raise IdError(f"subcommittee code {code!r} can't be used in an ID: give it a short code of upper-case letters "
                      "and digits (subcommittees.py SUBCOMMITTEES), e.g. 'LEG'")
    if code in RESERVED:
        raise IdError(f"subcommittee code {code!r} is reserved (a derived-ID qualifier or a record type): pick another")
    return code


def width(typ, code):
    return WIDTH.get((typ, code), DEFAULT_WIDTH.get(typ, 4))


def namespace(typ, code):
    """The regex of every ID of this (TYPE, CODE) namespace; group 1 is the number."""
    check_code(code)
    if code == BARE:
        return re.compile(rf"{typ}-(\d+)")
    return re.compile(rf"{typ}-{code}-(\d+)")


def make(typ, code, n):
    w = width(typ, code)
    return f"{typ}-{n:0{w}d}" if code == BARE else f"{typ}-{check_code(code)}-{n:0{w}d}"


def parse(rid):
    """-> (TYPE, CODE, number) for an allocated ID, else None (a derived ID, or not an ID)."""
    m = re.fullmatch(r"([A-Z]+)-(?:([A-Z][A-Z0-9]*)-)?(\d+)", rid or "")
    if not m or m.group(1) not in TYPES or (m.group(2) or "") in RESERVED:
        return None
    return m.group(1), m.group(2) or BARE, int(m.group(3))


def number(rid):
    return int(rid.rsplit("-", 1)[1])


class Allocator:
    """The next free ID per (TYPE, CODE), given every ID ever used (live and retired)."""

    def __init__(self, used=()):
        self.top = {}
        for rid in used:
            p = parse(rid)
            if p:
                k = p[:2]
                self.top[k] = max(self.top.get(k, 0), p[2])

    def next(self, typ, code):
        if typ not in TYPES:
            raise IdError(f"unknown record type {typ!r} (ids.TYPES)")
        k = (typ, check_code(code))
        self.top[k] = self.top.get(k, 0) + 1
        return make(typ, code, self.top[k])


def order(ids_in_order):
    """A sort key that keeps each namespace together, in the order the namespaces first appear in ids_in_order
    (the existing records), new namespaces after them by code, and numbers ascending inside each."""
    first = {}
    for rid in ids_in_order:
        p = parse(rid)
        if p and p[1] not in first:
            first[p[1]] = len(first)

    def key(rid):
        p = parse(rid)
        return (first.get(p[1], len(first)), p[1] if p[1] not in first else "", p[2])
    return key
