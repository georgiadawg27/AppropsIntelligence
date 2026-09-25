-- SQLite schema for the Data Dictionary (scoping doc): Account, Historical
-- Name, Source Document, Bill Report Reference, Appropriations Observation,
-- Account Relationship, Validation Record.
--
-- SQLite-specific choices, each because the default would silently accept
-- bad data:
--   * STRICT tables: without them a column declared INTEGER/BOOLEAN keeps
--     whatever it is given -- the workbook's 'TRUE'/'FALSE' strings would load
--     as text, and in SQLite 'TRUE' evaluates as false.
--   * Foreign keys are only enforced when a connection runs
--     PRAGMA foreign_keys = ON (approps_store.connect does; SQLite's default
--     is off, which is what makes "named the same" FKs possible).
--   * Booleans are INTEGER 0/1 with a CHECK; dates are ISO text with a CHECK
--     that they round-trip through date(); money is INTEGER whole dollars
--     (SQLite has no decimal type, and REAL would put float error into
--     amounts -- every pilot and pipeline amount is whole dollars).
--   * Identifiers are TEXT: the pilot uses readable ids (ACC-NASA-SCIENCE,
--     OBS-0001), the pipeline emits UUIDs. TEXT holds both.
--
-- Enums are CHECKs holding exactly the Data Dictionary's values (including
-- the 2026-09-25 additions: cbo_cost_estimate, provisional, superseded,
-- bill_report_reference_id).

CREATE TABLE account (
    canonical_account_id    TEXT PRIMARY KEY,
    canonical_name          TEXT NOT NULL,
    agency                  TEXT NOT NULL,
    bureau                  TEXT,
    treasury_account_symbol TEXT,
    status                  TEXT NOT NULL CHECK (status IN ('active', 'inactive', 'superseded')),
    fund_type               TEXT NOT NULL CHECK (fund_type IN ('general', 'trust', 'special', 'revolving',
                                                               'working_capital', 'no_year')),
    effective_start         TEXT NOT NULL CHECK (date(effective_start) IS effective_start),
    effective_end           TEXT CHECK (effective_end IS NULL OR date(effective_end) IS effective_end),
    -- Dictionary: array<string>. Stored as the workbook's ';'-separated display
    -- list; the loader checks it equals the historical_name rows, which are the
    -- source of truth (and the only thing matching reads).
    historical_names        TEXT,
    historical_identifiers  TEXT,
    -- not in the Data Dictionary; carried from the workbook
    subcommittee            TEXT NOT NULL,
    notes                   TEXT
) STRICT;

CREATE TABLE historical_name (
    historical_name_id   TEXT PRIMARY KEY,
    canonical_account_id TEXT NOT NULL REFERENCES account (canonical_account_id),
    former_name          TEXT NOT NULL,
    evidence             TEXT NOT NULL,
    approved_date        TEXT CHECK (approved_date IS NULL OR date(approved_date) IS approved_date),
    confidence           REAL NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    human_reviewed       INTEGER NOT NULL CHECK (human_reviewed IN (0, 1)),
    UNIQUE (canonical_account_id, former_name)
) STRICT;

CREATE TABLE source_document (
    document_id         TEXT PRIMARY KEY,
    source_agency       TEXT NOT NULL,
    url_or_identifier   TEXT NOT NULL,
    document_type       TEXT NOT NULL CHECK (document_type IN ('bill', 'committee_report', 'explanatory_statement',
                                                               'public_law', 'presidents_budget', 'budget_appendix',
                                                               'other', 'cbo_cost_estimate')),
    congress_session    TEXT,
    fiscal_year         INTEGER NOT NULL,
    publication_date    TEXT CHECK (publication_date IS NULL OR date(publication_date) IS publication_date),
    stage               TEXT NOT NULL CHECK (stage IN ('President''s Budget', 'House Reported', 'Senate Reported',
                                                       'Enacted', 'House Passed', 'Senate Passed')),
    -- 'YYYY-MM-DD' or 'YYYY-MM-DDTHH:MM:SS' (the pilot has both precisions)
    retrieval_timestamp TEXT CHECK (retrieval_timestamp IS NULL OR date(retrieval_timestamp) IS substr(retrieval_timestamp, 1, 10)),
    -- not in the Data Dictionary; carried from the workbook
    source_page         TEXT,
    also_covers         TEXT
) STRICT;

-- The bill / report for one subcommittee x fiscal year x stage (the
-- workbook's Bill Report Reference tab).
CREATE TABLE bill_report_reference (
    reference_id   TEXT PRIMARY KEY,
    subcommittee   TEXT NOT NULL,
    fiscal_year    INTEGER NOT NULL,
    stage          TEXT NOT NULL CHECK (stage IN ('President''s Budget', 'House Reported', 'Senate Reported',
                                                  'Enacted', 'House Passed', 'Senate Passed')),
    bill_id        TEXT,
    report_id      TEXT,
    bill_url       TEXT,
    report_jes_url TEXT,
    lookup_key     TEXT NOT NULL UNIQUE CHECK (lookup_key = subcommittee || '-' || fiscal_year || '-' || stage),
    UNIQUE (subcommittee, fiscal_year, stage)
) STRICT;

CREATE TABLE appropriations_observation (
    observation_id           TEXT PRIMARY KEY,
    canonical_account_id     TEXT NOT NULL REFERENCES account (canonical_account_id),
    fiscal_year              INTEGER NOT NULL,
    stage                    TEXT NOT NULL CHECK (stage IN ('President''s Budget', 'House Reported', 'Senate Reported',
                                                            'Enacted', 'House Passed', 'Senate Passed')),
    chamber                  TEXT CHECK (chamber IS NULL OR chamber IN ('House', 'Senate', 'N/A')),
    bill_id                  TEXT,
    report_id                TEXT,
    amount                   INTEGER NOT NULL,
    amount_type              TEXT NOT NULL CHECK (amount_type IN ('budget authority', 'obligation', 'outlay', 'rescission',
                                                                  'transfer', 'offsetting_collection', 'supplemental',
                                                                  'other')),
    -- which of an account's lines this is when it prints more than one of the
    -- same amount_type in a cell (NSF R&RA base vs 'defense'); NULL for the
    -- account's own line. Never '' -- a blank is NULL, so the fact key below
    -- can't tell two spellings of "none" apart.
    component                TEXT CHECK (component IS NULL OR component <> ''),
    offsetting_collections   INTEGER NOT NULL CHECK (offsetting_collections IN (0, 1)),
    transfer_link_account_id TEXT REFERENCES account (canonical_account_id),
    source_document_id       TEXT NOT NULL REFERENCES source_document (document_id),
    source_page              TEXT,
    source_table_or_section  TEXT,
    extraction_method        TEXT NOT NULL CHECK (extraction_method IN ('AI-extracted', 'human-entered', 'hybrid',
                                                                        'text-extracted')),
    confidence               REAL NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    -- provisional: an unconfirmed advance copy that passed every check;
    -- superseded: replaced by the official document's value (reconcile.py)
    verification_status      TEXT NOT NULL CHECK (verification_status IN ('unverified', 'auto-validated',
                                                                          'human-verified', 'flagged',
                                                                          'provisional', 'superseded')),
    -- The workbook derives bill_id / report_id / bill_url / report_jes_url by
    -- looking up (account.subcommittee, fiscal_year, stage) in Bill Report
    -- Reference. Stored as a real key; the loader checks bill_id / report_id
    -- agree with the row it points at.
    bill_report_reference_id TEXT REFERENCES bill_report_reference (reference_id)
) STRICT;

-- A fact's identity: one account's one amount type in one fiscal year and
-- stage, told apart by component and transfer counterpart where an account
-- has more than one. Section text is description, not identity. An
-- expression index, not UNIQUE(...): SQLite treats NULLs as distinct in a
-- UNIQUE constraint, so two base-line rows (component NULL) would never
-- collide.
CREATE UNIQUE INDEX observation_fact ON appropriations_observation (
    canonical_account_id, fiscal_year, stage, amount_type,
    ifnull(component, ''), ifnull(transfer_link_account_id, ''));

CREATE INDEX observation_by_account ON appropriations_observation (canonical_account_id, fiscal_year, stage);
CREATE INDEX observation_by_document ON appropriations_observation (source_document_id);

CREATE TABLE account_relationship (
    relationship_id       TEXT PRIMARY KEY,
    from_account_id       TEXT NOT NULL REFERENCES account (canonical_account_id),
    to_account_id         TEXT NOT NULL REFERENCES account (canonical_account_id),
    relationship_type     TEXT NOT NULL CHECK (relationship_type IN ('same', 'renamed', 'split_from', 'merged_into',
                                                                      'consolidated', 'moved_reclassified', 'uncertain')),
    effective_fiscal_year INTEGER,
    evidence              TEXT NOT NULL,
    confidence            REAL NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    human_reviewed        INTEGER NOT NULL CHECK (human_reviewed IN (0, 1)),
    CHECK (from_account_id <> to_account_id)
) STRICT;

CREATE TABLE validation_record (
    validation_id       TEXT PRIMARY KEY,
    observation_id      TEXT NOT NULL REFERENCES appropriations_observation (observation_id),
    rule_applied        TEXT NOT NULL CHECK (rule_applied IN ('source_text', 'structural', 'table_total', 'cross_document',
                                                              'historical', 'account_identity', 'unit', 'semantic')),
    expected_result     TEXT,
    observed_result     TEXT,
    result              TEXT NOT NULL CHECK (result IN ('pass', 'fail', 'flag')),
    human_review_status TEXT CHECK (human_review_status IS NULL OR human_review_status IN ('pending', 'resolved')),
    reviewer            TEXT,
    resolution          TEXT
) STRICT;

CREATE INDEX validation_by_observation ON validation_record (observation_id);

-- A fact a document was checked for and confirmed not to print: no such
-- line (or the line printed blank in that column). Distinct from a missing
-- fact (nobody has looked, or the document isn't held) and from a $0
-- observation (printed, funded at zero). Same fact identity as an
-- observation; a fact is one or the other, never both (triggers below).
CREATE TABLE confirmed_absence (
    confirmed_absence_id TEXT PRIMARY KEY,
    canonical_account_id TEXT NOT NULL REFERENCES account (canonical_account_id),
    fiscal_year          INTEGER NOT NULL,
    stage                TEXT NOT NULL CHECK (stage IN ('President''s Budget', 'House Reported', 'Senate Reported',
                                                        'Enacted', 'House Passed', 'Senate Passed')),
    amount_type          TEXT NOT NULL CHECK (amount_type IN ('budget authority', 'obligation', 'outlay', 'rescission',
                                                              'transfer', 'offsetting_collection', 'supplemental',
                                                              'other')),
    component            TEXT CHECK (component IS NULL OR component <> ''),
    source_document_id   TEXT NOT NULL REFERENCES source_document (document_id),
    evidence             TEXT NOT NULL,
    confirmed_date       TEXT CHECK (confirmed_date IS NULL OR date(confirmed_date) IS confirmed_date)
) STRICT;

CREATE UNIQUE INDEX confirmed_absence_fact ON confirmed_absence (
    canonical_account_id, fiscal_year, stage, amount_type, ifnull(component, ''));

CREATE TRIGGER absence_not_observed BEFORE INSERT ON confirmed_absence
WHEN EXISTS (SELECT 1 FROM appropriations_observation o
             WHERE o.canonical_account_id = NEW.canonical_account_id AND o.fiscal_year = NEW.fiscal_year
               AND o.stage = NEW.stage AND o.amount_type = NEW.amount_type
               AND ifnull(o.component, '') = ifnull(NEW.component, ''))
BEGIN
    SELECT RAISE(ABORT, 'confirmed absence contradicts an observation of the same fact');
END;

CREATE TRIGGER observation_not_absent BEFORE INSERT ON appropriations_observation
WHEN EXISTS (SELECT 1 FROM confirmed_absence a
             WHERE a.canonical_account_id = NEW.canonical_account_id AND a.fiscal_year = NEW.fiscal_year
               AND a.stage = NEW.stage AND a.amount_type = NEW.amount_type
               AND ifnull(a.component, '') = ifnull(NEW.component, ''))
BEGIN
    SELECT RAISE(ABORT, 'observation contradicts a confirmed absence of the same fact');
END;
