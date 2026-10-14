"""
The rules registry: the subcommittee- and account-specific rules, kept out of the core.

The core (scripts/tag_components.py, scripts/check_legislative_text.py, scripts/measure_reconcile.py,
scripts/appendix_attributes.py, subcommittees.py) holds only the general rules. Everything that is true of one
subcommittee's documents or accounts lives in a rule file, reference/rules/<CODE>.py (plus any folder named in
APPROPS_RULES_DIRS, paths.py), loaded here. A rule file is code: each rule may carry a function, data, or both.

    CODE = "LHHS"
    RULES = [Rule("LHHS-PHRASE-CURES", hook="phrase", scope=Scope("LHHS", accounts=ANY, fiscal_years=ANY,
                  stages=ANY), fn=..., moved_from="scripts/tag_components.py CURES", doc="...")]

HOOKS below are the only places a rule can act, each called from one place in the core. A rule fires only inside
its scope (subcommittee, accounts, fiscal years, stages: each stated, ANY where it is not limited). Two rules that
act on the same line stop the run (RuleConflict) -- never a silent precedence; a rule that also acts where a core
rule acts must name that core rule in `overrides`, or that stops the run too. A rule id found in two rule files
stops the load: a rule two subcommittees use belongs in the core (reference/ADDING_A_SUBCOMMITTEE.md, step 7).
What neither the core nor a rule accounts for becomes a review item in the core's report (tag_components'
"untagged", intake's review items) -- it is never pushed into the nearest rule.
"""

import importlib.util
from dataclasses import dataclass, field
from pathlib import Path

import paths

# hook -> what a rule there can change, and where the core calls it
HOOKS = {
    "display": "data {'name': ...}: the subcommittee's display name (subcommittees.display_name)",
    "layout": "data: how the subcommittee's bills print its agencies -- {'agency_words': {agency: words}, "
              "'bureau_words': {bureau: words}, 'agency_aliases': {agency: [words]}} "
              "(check_legislative_text.agency_words / in_context: which paragraph is an account's)",
    "appendix": "data {'chapters': (...)}: the Budget Appendix chapters the subcommittee's accounts are in "
                "(appendix_attributes)",
    "extract": "accounts scope only: an account held only as its agency total, whose headline is read from the "
               "agency's one appropriation paragraph (tag_components.total_only_outcomes)",
    "list_item": "fn(x, ctx) -> (type, designation, transfer_from, rule) | None: an amount that is an item of a list "
                 "a rule reads (ctx.state carries the list across the paragraph's amounts); beside the main amount "
                 "(tag_components.read_amounts, before the phrase rules)",
    "phrase": "fn(x, ctx) -> (type, designation, transfer_from, rule) | NotComponent(kind) | None: one amount of a "
              "paragraph read as a component, or vetoed (tag_components.classify; ctx.state is per paragraph)",
    "past_window": "fn(x, ctx) -> (type, designation, transfer_from, rule) | None: an amount nothing classifies, read "
                   "on past the phrase window in the paragraph's own text (tag_components.read_amounts)",
    "transfer_source": "fn(x, t, ctx) -> (transfer_from, rule) | None: the source of a transfer the core's transfer "
                       "rule read (t: its match) (tag_components.classify)",
    "not_component": "fn(x, ctx) -> kind | None: a phrase that looks like a component but is not; counted, never "
                     "tagged (tag_components.not_a_component)",
    "line": "fn(o, ctx) -> (type, designation, transfer_from, rule) | None: a held table line re-tagged as a "
            "component of its headline (tag_components.retag)",
    "table_line": "fn(row, ctx) -> {component_type, tag_rule, notes} | None: a row of table_lines.csv the core's "
                  "CDS rule doesn't read (tag_components.table_lines)",
    "components": "fn(data, heads, report, ctx) -> [component records]: components from the rule's own source "
                  "(tag_components.build, with the other line sources)",
    "advances": "accounts scope only: an account that appropriates an advance for the next fiscal year's first "
                "quarter; its fiscal_years' first year is the first that holds an advance available "
                "(tag_components.advances, request_text)",
    "carry_forward": "fn(c, ctx) -> {'anomaly': (section, stated)} | {'rule': text} | {'note': text} | None: a full-"
                     "year CR's change to one carried component (tag_components.carry_forward); data: the anomalies "
                     "(measure_reconcile's cr_anomaly_dropped reads them)",
    "checks": "data {'known_causes', 'cell_causes', 'cause_text', 'member_cause'} and fn view_check(a, v, h, comps, "
              "ctx) -> (computed, used, expected) | None: measure_reconcile's causes and a printed line it checks "
              "its own way (measure_reconcile.run / why)",
    "absence": "fn(account, fiscal_year, stage, ctx) -> reason | None: a missing figure that is neither an absence "
               "nor a blank (intake's missing-figure check)",
}


class _Any:
    def __repr__(self):
        return "ANY"


ANY = _Any()


class RuleError(Exception):
    """A rule file that can't be loaded as it stands."""


class RuleConflict(Exception):
    """Two rules (or a rule and a core rule it doesn't override) act on the same line."""


@dataclass(frozen=True)
class NotComponent:
    kind: str


@dataclass(frozen=True)
class Scope:
    subcommittee: str
    accounts: object = None          # ANY or a set of canonical_account_id
    fiscal_years: object = None      # ANY or (first, last), either end None for open
    stages: object = None            # ANY or a set of stages

    def check(self):
        for k in ("accounts", "fiscal_years", "stages"):
            if getattr(self, k) is None:
                raise RuleError(f"scope {self.subcommittee}: {k} not stated (ANY where it is not limited)")

    def contains(self, sc, account=None, fy=None, stage=None):
        if sc != self.subcommittee:
            return False
        if self.accounts is not ANY and account not in self.accounts:
            return False
        if self.fiscal_years is not ANY:
            if fy is None:
                return False
            lo, hi = self.fiscal_years
            if (lo is not None and int(fy) < lo) or (hi is not None and int(fy) > hi):
                return False
        if self.stages is not ANY and stage not in self.stages:
            return False
        return True

    def describe(self):
        fys = "any FY" if self.fiscal_years is ANY else \
            f"FY{self.fiscal_years[0] or '...'}-FY{self.fiscal_years[1] or '...'}"
        accs = "any account" if self.accounts is ANY else ", ".join(sorted(self.accounts))
        sts = "any stage" if self.stages is ANY else ", ".join(sorted(self.stages))
        return f"{self.subcommittee}; {accs}; {fys}; {sts}"


@dataclass(frozen=True)
class Rule:
    id: str
    hook: str
    scope: Scope
    fn: object = None
    data: object = None
    overrides: tuple = ()            # core rules this one acts in place of, by name (tag_components.CORE_PHRASE)
    moved_from: str = ""
    doc: str = ""


@dataclass
class Ctx:
    """What a rule is told about where it is: the cell, the account, the paragraph, and the core helpers (tc: the
    tagger module, for segments() and the like)."""
    subcommittee: str
    account: str = None
    fiscal_year: int = None
    stage: str = None
    para: dict = None
    state: dict = field(default_factory=dict)
    tc: object = None


class Registry:
    def __init__(self, rules=()):
        self.all = list(rules)
        self.codes = sorted({r.scope.subcommittee for r in self.all})
        self.modules = {}               # code -> the loaded rule file (load())
        self.by_hook = {}
        for r in self.all:
            if r.hook not in HOOKS:
                raise RuleError(f"{r.id}: unknown hook {r.hook!r} (rules_registry.HOOKS)")
            r.scope.check()
            self.by_hook.setdefault(r.hook, []).append(r)

    @classmethod
    def load(cls, dirs=None):
        rules, seen, codes, modules = [], {}, {}, {}
        for d in dirs or paths.rules_dirs():
            for p in sorted(Path(d).glob("*.py")):
                if p.name.startswith("_"):
                    continue
                spec = importlib.util.spec_from_file_location(f"approps_rules_{p.stem}", p)
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                code = getattr(mod, "CODE", None)
                if code != p.stem:
                    raise RuleError(f"{p}: CODE {code!r} is not the file's name")
                if code in codes:
                    raise RuleError(f"{p}: subcommittee {code} already has a rule file ({codes[code]})")
                codes[code] = p
                modules[code] = mod
                for r in getattr(mod, "RULES", []):
                    if r.scope.subcommittee != code:
                        raise RuleError(f"{p}: rule {r.id} is scoped to {r.scope.subcommittee}, not {code}")
                    if r.id in seen:
                        raise RuleError(f"rule {r.id} is in two rule files ({seen[r.id]}, {p}): a rule two "
                                        "subcommittees use moves to the core (reference/ADDING_A_SUBCOMMITTEE.md)")
                    seen[r.id] = p
                    rules.append(r)
        reg = cls(rules)
        reg.codes = sorted(codes)
        reg.modules = modules
        return reg

    def rules(self, hook, sc, account=None, fy=None, stage=None):
        return [r for r in self.by_hook.get(hook, []) if r.scope.contains(sc, account, fy, stage)]

    def first(self, hook, line, ctx, *args):
        """Run every in-scope rule of the hook on one line -> (rule, result) of the one that acts, or None.
        Two that act: RuleConflict."""
        hits = []
        for r in self.rules(hook, ctx.subcommittee, ctx.account, ctx.fiscal_year, ctx.stage):
            if r.fn is None:
                continue                # a data-only rule of the hook
            got = r.fn(*args, ctx)
            if got is not None:
                hits.append((r, got))
        if len(hits) > 1:
            raise RuleConflict(f"{line}: rules {', '.join(r.id for r, _ in hits)} all act on it ({hook}) -- narrow "
                               "their scopes; there is no precedence between rules")
        return hits[0] if hits else None

    def data(self, hook, sc, account=None, fy=None, stage=None):
        """The data of the in-scope rules of a data hook, merged; one key set by two rules: RuleConflict."""
        out, by = {}, {}
        for r in self.rules(hook, sc, account, fy, stage):
            for k, v in (r.data or {}).items():
                if k in out and not isinstance(v, dict):
                    raise RuleConflict(f"{sc} {hook}: {k} set by {by[k]} and {r.id}")
                if isinstance(v, dict):
                    cur = out.setdefault(k, {})
                    for kk in v:
                        if kk in cur:
                            raise RuleConflict(f"{sc} {hook}: {k}[{kk!r}] set by {by[k]} and {r.id}")
                    cur.update(v)
                else:
                    out[k] = v
                by[k] = r.id
        return out


_ACTIVE = []


def active():
    """The registry the run uses: reference/rules/ (+ APPROPS_RULES_DIRS), loaded once."""
    if not _ACTIVE:
        _ACTIVE.append(Registry.load())
    return _ACTIVE[0]


def use(registry):
    """Make a registry the active one (tests: an empty registry is the core alone)."""
    _ACTIVE[:] = [registry]
    return registry


def reset():
    _ACTIVE.clear()
