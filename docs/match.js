/*
 * match.js -- account-name matching in the browser, for the static export.
 *
 * A line-for-line port of accounts.best_match / allowed_distance / norm and
 * approps_store.resolve / split_agency / agency_aliases (agency=None path),
 * run over the fixed account list the export writes to index.json. The
 * rules and constants are the Python ones; tests/test_static.py runs this
 * file under node against approps_store.resolve() over thousands of queries
 * and requires identical answers.
 */
(function (root) {
  "use strict";
  // accounts.py constants (index.json carries the values the export read)
  let ACCEPT_MAX_DISTANCE = 2, ACCEPT_MAX_FRACTION = 0.15, AMBIGUITY_MARGIN = 2;
  const STOPWORDS = new Set(["and", "of", "the", "for", "on", "in"]);
  const AGENCY_TOTAL_RE = /\s*\(agency total\)\s*$/i;

  function configure(c) {
    if (!c) return;
    ACCEPT_MAX_DISTANCE = c.accept_max_distance;
    ACCEPT_MAX_FRACTION = c.accept_max_fraction;
    AMBIGUITY_MARGIN = c.ambiguity_margin;
  }

  const norm = (s) => (s || "").toLowerCase().replace(/[^a-z]/g, "");

  function distance(a, b) {
    if (a === b) return 0;
    if (a.length < b.length) [a, b] = [b, a];
    let prev = Array.from({ length: b.length + 1 }, (_, j) => j);
    for (let i = 1; i <= a.length; i++) {
      const cur = [i];
      for (let j = 1; j <= b.length; j++) {
        cur.push(Math.min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (a[i - 1] !== b[j - 1] ? 1 : 0)));
      }
      prev = cur;
    }
    return prev[b.length];
  }

  function allowedDistance(canonicalNorm) {
    if (canonicalNorm.length < 7) return 0;
    return Math.min(ACCEPT_MAX_DISTANCE, Math.floor(ACCEPT_MAX_FRACTION * canonicalNorm.length));
  }

  // Python's min() over (distance, name) tuples: smallest distance, then the
  // smaller name by code point
  const tupleLess = (x, y) => x[0] < y[0] || (x[0] === y[0] && x[1] < y[1]);

  function bestMatch(text, candidates, namesOf) {
    const t = norm(text);
    if (!t || !candidates.length) return ["unmatched", null, null, null];
    const scored = [];
    candidates.forEach((c, i) => {
      const names = namesOf(c).filter((n) => norm(n));
      if (!names.length) return;
      let best = null;
      for (const n of names) {
        const cand = [distance(t, norm(n)), n];
        if (!best || tupleLess(cand, best)) best = cand;
      }
      scored.push([best[0], i, c, best[1]]);
    });
    if (!scored.length) return ["unmatched", null, null, null];
    scored.sort((x, y) => x[0] - y[0] || x[1] - y[1]);
    const [d, , bestC, name] = scored[0];
    const runner = scored.length > 1 ? scored[1][0] : null;
    if (d === 0) return runner !== 0 ? ["exact", bestC, 0, name] : ["ambiguous", null, 0, null];
    if (d > allowedDistance(norm(name))) return ["unmatched", null, d, null];
    if (runner !== null && runner < d + AMBIGUITY_MARGIN) return ["ambiguous", null, d, null];
    return ["ocr_corrected", bestC, d, name];
  }

  function agencyAliases(agency) {
    const words = agency.match(/[A-Za-z]+/g) || [];
    return new Set([
      norm(agency),
      words.filter((w) => !STOPWORDS.has(w.toLowerCase())).map((w) => w[0]).join("").toLowerCase(),
      words.map((w) => w[0]).join("").toLowerCase(),
    ]);
  }

  // Python str.split(): runs of whitespace, no empty strings
  const pySplit = (s) => s.split(/\s+/).filter(Boolean);

  function splitAgency(text, agencies) {
    const words = pySplit(text);
    for (let k = words.length - 1; k > 0; k--) {
      const head = norm(words.slice(0, k).join(" "));
      const hits = agencies.filter((ag) => agencyAliases(ag).has(head));
      if (hits.length === 1) return [hits[0], words.slice(k).join(" ")];
    }
    return [null, text];
  }

  const namesOf = (a) => [a.canonical_name].concat(a.historical_names);

  /* accounts: [{canonical_account_id, canonical_name, agency, historical_names}]
   * sorted by canonical_account_id (as accounts_for_matching returns them).
   * -> the same dict approps_store.resolve() returns (account as given). */
  function resolve(accounts, text) {
    const agencies = Array.from(new Set(accounts.map((a) => a.agency))).sort();
    let [agency, name] = splitAgency(text, agencies);
    const whole = agencies.filter((ag) => agencyAliases(ag).has(norm(text)));
    if (whole.length === 1) {
      const totals = accounts.filter((a) => a.agency === whole[0] && AGENCY_TOTAL_RE.test(a.canonical_name));
      if (totals.length === 1) {
        return { query: text, agency: whole[0], name: text, match: "exact", distance: 0,
                 matched_name: whole[0], account: totals[0], via: "agency_total", candidates: [] };
      }
    }
    const scoped = accounts.filter((a) => agency === null || a.agency === agency);
    const [kind, acct, d, matched] = bestMatch(name, scoped, namesOf);
    const out = { query: text, agency, name, match: kind, distance: d, matched_name: matched,
                  account: acct, via: null, candidates: [] };
    if (acct) {
      out.via = matched === acct.canonical_name ? "canonical" : "historical_name";
    } else {
      const t = norm(name);
      const scored = scoped.map((a) => {
        let best = null;
        for (const n of namesOf(a)) {
          const cand = [distance(t, norm(n)), n];
          if (!best || tupleLess(cand, best)) best = cand;
        }
        return [best, a];
      });
      const top = scored.length ? Math.min(...scored.map((s) => s[0][0])) : 0;
      for (const [best, a] of scored) {
        if (best[0] <= top + AMBIGUITY_MARGIN) {
          out.candidates.push({ canonical_account_id: a.canonical_account_id, canonical_name: a.canonical_name,
                                agency: a.agency, distance: best[0], name: best[1] });
        }
      }
      out.candidates.sort((x, y) => x.distance - y.distance ||
        (x.canonical_account_id < y.canonical_account_id ? -1 : x.canonical_account_id > y.canonical_account_id ? 1 : 0));
    }
    return out;
  }

  const api = { configure, norm, distance, allowedDistance, bestMatch, agencyAliases, splitAgency, resolve };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.ApprMatch = api;
})(typeof self !== "undefined" ? self : this);
