"""
Where the pipeline reads its data and rules from -- one place, so the same code can run on another data root
(the synthetic subcommittee of tests/test_any_subcommittee.py) without a code edit.

    APPROPS_DATA_ROOT    a folder laid out like the repository's data: data/staged/, data/legislative_text/,
                         reference/review/accounting/ (the tagger's line files). Default: the repository.
    APPROPS_RULES_DIRS   more rule-file folders (os.pathsep-separated), read after reference/rules/
                         (rules_registry.py). Default: none.

Nothing else changes with them: the code, the vocabularies and the parser stay the repository's.
"""

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def data_root():
    return Path(os.environ.get("APPROPS_DATA_ROOT") or ROOT)


def staged():
    return data_root() / "data" / "staged"           # the canonical data, split (staged_data.py)


def text_dir():
    return data_root() / "data" / "legislative_text"


def accounting_dir():
    return data_root() / "reference" / "review" / "accounting"


RULES_DIR = ROOT / "reference" / "rules"


def rules_dirs():
    extra = [Path(p) for p in (os.environ.get("APPROPS_RULES_DIRS") or "").split(os.pathsep) if p]
    return [RULES_DIR] + extra
