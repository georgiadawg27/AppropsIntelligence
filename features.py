"""
features.py

The page's feature flags (reference/ui/explore-v1/SPEC.md section 9): one flag per feature in
web/ui_contract.json, true only if every data field the contract lists for it holds a value somewhere
in the data the page reads. One rule for both serving modes:

  - export_static.py writes the flags into docs/data/index.json, computed from the files it exported;
  - approps_web.py serves them at GET /api/features, computed from the payloads it serves
    (the same answers the export writes to docs/data/).

Computed from the data, never set by hand.
"""

import fnmatch
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CONTRACT = ROOT / "web" / "ui_contract.json"        # the data fields each page feature reads


def contract():
    return json.loads(CONTRACT.read_text())


def field_values(obj, path):
    """Every value at a dotted path in obj (web/ui_contract.json's path_syntax): 'name[]' steps into each
    item of a list, '*' into each value of an object (and '*[]' into each item of those)."""
    found = [obj]
    for step in path.split("."):
        key, each = (step[:-2], True) if step.endswith("[]") else (step, False)
        nxt = []
        for o in found:
            if not isinstance(o, dict):
                continue
            if key == "*":
                nxt.extend(o.values())
            elif key in o:
                nxt.append(o[key])
        if each:
            nxt = [item for o in nxt if isinstance(o, list) for item in o]
        found = nxt
    return found


def held(value):
    """A field holds data unless it is null, empty text or an empty list/object. 0 and False hold data."""
    return value is not None and value != "" and value != [] and value != {}


def flags(paths, load, spec=None):
    """{"features": {name: bool}, "contract_version": n} for the data files `paths` (paths relative to
    docs/data/, as the contract names them: "index.json", "subcommittees/LHHS.json", ...); load(path)
    gives a file's JSON. A contract pattern matches paths as a shell glob in which '*' stays inside one
    folder, as Path.glob does."""
    spec = spec or contract()
    loaded = {}

    def docs(pattern):
        if pattern not in loaded:
            want = pattern.count("/")
            loaded[pattern] = [load(p) for p in sorted(paths)
                               if p.count("/") == want and fnmatch.fnmatchcase(p, pattern)]
        return loaded[pattern]

    def present(field):
        pattern, path = field.split(":", 1)
        return any(held(v) for doc in docs(pattern) for v in field_values(doc, path))

    out = {name: bool(f["fields"]) and all(present(x) for x in f["fields"]) for name, f in spec["features"].items()}
    return {"features": out, "contract_version": spec["contract_version"]}


def index_accounts(pool):
    """index.json's "accounts" (the matching pool the page searches), from approps_store.accounts_for_matching()."""
    return [{"canonical_account_id": a["canonical_account_id"], "canonical_name": a["canonical_name"],
             "agency": a["agency"], "historical_names": a["historical_names"]} for a in pool]


def as_served(obj):
    """obj as a client reads it: through JSON, the way both modes write it (dates and the like as text)."""
    return json.loads(json.dumps(obj, default=str))
