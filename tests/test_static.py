"""
The static export (export_static.py -> docs/): the same answers as the live
UI, from pre-generated JSON and browser-side matching.

Run:  python -m unittest tests.test_static -v

Matcher parity runs web/match.js under node (skipped without node); browser
tests need playwright + chromium, as tests/test_web.py.
"""

import functools
import hashlib
import http.server
import json
import multiprocessing
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "scripts"))

import approps_store as S  # noqa: E402
import approps_web as W  # noqa: E402
import export_static as E  # noqa: E402
import workbook_digest as WD  # noqa: E402
from test_web import WORKBOOK, chromium_path, sync_playwright  # noqa: E402

NODE = shutil.which("node")


def fresh_export(out):
    return E.export(WORKBOOK, out)


def python_answers(db, queries):
    """approps_store.resolve()'s answers for a slice of the queries (one worker's share)."""
    conn = S.connect(db, readonly=True)
    try:
        return [python_answer(S.resolve(conn, q)) for q in queries]
    finally:
        conn.close()


def typed_queries(pool):
    """Queries a user could type, each with its kind: every name as is, with its agency in
    front, and one- and two-edit misspellings of each; agencies alone; junk. {query: kind}
    (a query two recipes produce keeps the first kind)."""
    qs = {}

    def add(kind, *qq):
        for q in qq:
            qs.setdefault(q, kind)
    agencies = sorted({a["agency"] for a in pool})
    for a in pool:
        for n in [a["canonical_name"]] + a["historical_names"]:
            add("name", n, n.lower(), n.upper(), "  " + n + "  ")
            add("agency_prefixed", f"NASA {n}", f"NSF {n}", f"DOJ {n}", f"{a['agency']} {n}")
            letters = [i for i, ch in enumerate(n) if ch.isalpha()]
            for k, i in enumerate(letters):
                add("deletion", n[:i] + n[i + 1:])
                add("substitution", n[:i] + ("x" if n[i] != "x" else "q") + n[i + 1:])
                if k % 2 == 0 and i + 3 < len(n):
                    add("transposition", n[:i] + n[i + 1] + n[i] + n[i + 2:])     # two edits
                    add("two_substitutions", n[:i] + "zz" + n[i + 2:])
    for ag in agencies:
        add("agency", ag, ag.lower(), "".join(w[0] for w in ag.split() if w.lower() not in S.STOPWORDS))
    add("hand_picked", "Office of Inspector General", "Nothing Like Any Account", "a", "NASA", "NSF", "National",
        "Science Science", "Deep Spaee Exploratlon Systems", "Scince", "Space Operation", "Educaton",
        "<img src=x>", "Exploration Technology", "Space Tech", "Defense function", "STEM")
    return qs


def names_and_variants(pool):
    return sorted(typed_queries(pool))


PARITY_SEED = 20261007


def parity_sample(typed, size, seed=PARITY_SEED):
    """A fixed, seeded sample of about `size` queries spread across the kinds: each kind in
    proportion to its share, at least 50 of it (all of a smaller kind); the agency and
    hand-picked queries always in full."""
    import random
    by_kind = {}
    for q, k in sorted(typed.items()):
        by_kind.setdefault(k, []).append(q)
    rng = random.Random(seed)
    total = len(typed)
    out = []
    for k, qs in sorted(by_kind.items()):
        if k in ("agency", "hand_picked"):
            out += qs
            continue
        n = min(len(qs), max(50, round(size * len(qs) / total)))
        out += rng.sample(qs, n)
    return sorted(out)


def python_answer(res):
    return {"query": res["query"], "agency": res["agency"], "name": res["name"], "match": res["match"],
            "distance": res["distance"], "matched_name": res["matched_name"], "via": res["via"],
            "account": res["account"]["canonical_account_id"] if res["account"] else None,
            "candidates": res["candidates"]}


class StaticTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.out = Path(cls.tmp.name) / "docs"
        fresh_export(cls.out)
        cls.index = json.loads((cls.out / "data" / "index.json").read_text())
        cls.db = Path(cls.tmp.name) / "approps.db"
        S.load(WORKBOOK, cls.db)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()


class Export(StaticTest):
    def test_every_account_file_is_the_live_api_payload(self):
        ids = [a["canonical_account_id"] for a in self.index["accounts"]]
        self.assertEqual(len(ids), 142)                  # v33: 30 CJS + 100 Labor-HHS; D-7: + 12 title and bill totals
        self.assertEqual(sorted(p.stem for p in (self.out / "data" / "accounts").glob("*.json")), sorted(ids))
        for aid in ids:
            live = json.loads(json.dumps(W.account(str(self.db), aid), default=str))
            got = json.loads((self.out / "data" / "accounts" / f"{aid}.json").read_text())
            self.assertEqual(got, {"history": live["history"], "grid": live["grid"]}, aid)

    def test_index_is_the_matching_pool(self):
        conn = S.connect(self.db, readonly=True)
        pool = S.accounts_for_matching(conn)
        conn.close()
        self.assertEqual([(a["canonical_account_id"], a["canonical_name"], a["agency"], a["historical_names"])
                          for a in pool],
                         [(a["canonical_account_id"], a["canonical_name"], a["agency"], a["historical_names"])
                          for a in self.index["accounts"]])
        self.assertEqual(self.index["source"]["workbook"], WORKBOOK.name)
        self.assertEqual(self.index["source"]["warnings"], S.load(WORKBOOK, Path(self.tmp.name) / "again.db")["warnings"])

    def test_deterministic_and_stale_files_removed(self):
        with tempfile.TemporaryDirectory() as d:
            again = Path(d) / "docs"
            fresh_export(again)
            (again / "data" / "accounts" / "ACC-GONE.json").write_text("{}")
            fresh_export(again)
            self.assertFalse((again / "data" / "accounts" / "ACC-GONE.json").exists())
            a = {p.relative_to(self.out): p.read_bytes() for p in self.out.rglob("*") if p.is_file()}
            b = {p.relative_to(again): p.read_bytes() for p in again.rglob("*") if p.is_file()}
            self.assertEqual(a, b)

    def test_page_is_the_live_page_marked_static(self):
        live = (ROOT / "web" / "index.html").read_text()
        static = (self.out / "index.html").read_text()
        self.assertEqual(static, live.replace("<head>", "<head>\n" + E.DATA_META, 1))
        self.assertEqual((self.out / "match.js").read_text(), (ROOT / "web" / "match.js").read_text())

    def test_page_can_read_the_data_from_another_address(self):
        # the R2 copy's Worker (platform plan, Phase 1): an https:// address ending in "/", nothing else
        self.assertEqual(E.data_meta(), E.DATA_META)
        self.assertEqual(E.data_meta("https://approps-data.example.workers.dev/data/"),
                         '<meta name="approps-data" content="https://approps-data.example.workers.dev/data/">')
        for bad in ("http://x.example/data/", "https://x.example/data", "javascript:alert(1)//", 'https://x.example/"a/'):
            with self.assertRaises(SystemExit, msg=bad):
                E.data_meta(bad)

    def test_brand_files_are_exported_and_the_icons_resolve(self):
        # web/brand/ is copied byte for byte, and every icon the page's <head> links to is in the export
        src = {p.name: p.read_bytes() for p in (ROOT / "web" / "brand").iterdir() if p.suffix in (".svg", ".png")}
        got = {p.name: p.read_bytes() for p in (self.out / "brand").iterdir()}
        self.assertEqual(got, src)
        page = (self.out / "index.html").read_text()
        hrefs = re.findall(r'<link rel="(?:icon|apple-touch-icon)" href="([^"]+)"', page)
        self.assertEqual(len(hrefs), 5)
        for href in hrefs:
            self.assertTrue((self.out / href).is_file(), href)

    def test_commit_date_does_not_depend_on_the_git_version(self):
        # git 2.55 (the Actions runner) renders %cI as ...Z, older git as
        # ...+00:00; the export must not flip between them
        def fake_git(cmd, **kw):
            fmt = next(a for a in cmd if a.startswith("--format="))
            out = {"--format=%cI": "2026-09-25T20:55:20Z", "--format=%ct": "1790369720"}[fmt]
            return subprocess.CompletedProcess(cmd, 0, stdout=out + "\n", stderr="")
        with mock.patch.object(E.subprocess, "run", fake_git):
            self.assertEqual(E.committed_date(WORKBOOK), "2026-09-25T20:55:20+00:00")

    def test_committed_docs_are_current(self):
        # docs/ is what the export makes from the committed data and web/ (the Actions workflow
        # regenerates it on push; this catches a stale copy): every file byte for byte, except the
        # Download workbook. Only CI's export job on main writes that file (pull requests don't commit
        # it), so here its CONTENT is compared, not its bytes: every cell's value and formula on every
        # sheet of the export's workbook against tests/fixtures/workbook_values.json, the digest the
        # data's own commit carries (scripts/workbook_digest.py)
        committed = ROOT / "docs"
        a = {p.relative_to(self.out): p.read_bytes() for p in self.out.rglob("*") if p.is_file()}
        b = {p.relative_to(committed): p.read_bytes() for p in committed.rglob("*") if p.is_file()}
        self.assertEqual(sorted(a), sorted(b))
        self.assertEqual([k for k in a if a[k] != b[k] and k != Path(E.DOWNLOAD)], [])
        want = json.loads((ROOT / "tests" / "fixtures" / "workbook_values.json").read_text())
        self.assertEqual(want["data_sha256"], hashlib.sha256(S.STAGED.read_bytes()).hexdigest(),
                         "tests/fixtures/workbook_values.json is from other data: regenerate it")
        self.assertEqual(WD.compare(WD.digest(self.out / E.DOWNLOAD), want["sheets"]), [])


def contract_fields_present(data_dir, contract):
    """{field: held anywhere in data_dir?} for every field web/ui_contract.json names, read back from the
    written files (index.json included) by a walker of its own, not export_static's."""
    def walk(obj, steps):
        if not steps:
            yield obj
            return
        step, rest = steps[0], steps[1:]
        key = step[:-2] if step.endswith("[]") else step
        if not isinstance(obj, dict):
            return
        values = list(obj.values()) if key == "*" else ([obj[key]] if key in obj else [])
        for v in values:
            if step.endswith("[]"):
                for item in (v if isinstance(v, list) else []):
                    yield from walk(item, rest)
            else:
                yield from walk(v, rest)

    out = {}
    for feature in contract["features"].values():
        for field in feature["fields"]:
            pattern, path = field.split(":", 1)
            docs = [json.loads(p.read_text()) for p in sorted(data_dir.glob(pattern))]
            if pattern == "subcommittees/*.json":
                # a split subcommittee is published by year, not whole: its grid put back together
                docs += [E.read_grid(data_dir, f.name) for f in sorted((data_dir / "subcommittees").glob("*/"))
                         if (f / "index.json").exists()]
            out[field] = any(v not in (None, "", [], {}) for d in docs for v in walk(d, path.split(".")))
    return out


class Features(StaticTest):
    """docs/data/index.json "features": computed from web/ui_contract.json and the exported data
    (reference/ui/explore-v1/SPEC.md section 9)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.contract = json.loads((ROOT / "web" / "ui_contract.json").read_text())
        cls.present = contract_fields_present(cls.out / "data", cls.contract)

    def test_features_are_what_the_contract_and_the_data_give(self):
        want = {name: all(self.present[x] for x in f["fields"]) for name, f in self.contract["features"].items()}
        self.assertEqual(self.index["features"], want)
        self.assertEqual(self.index["contract_version"], self.contract["contract_version"])
        committed = json.loads((ROOT / "docs" / "data" / "index.json").read_text())
        self.assertEqual((committed["features"], committed["contract_version"]),
                         (want, self.contract["contract_version"]))

    def test_live_api_features_agree_with_the_export(self):
        # approps_web.py's /api/features reads the payloads it serves; the export, the files it wrote from the
        # same answers: the two modes give the page the same flags
        live = W.features(str(self.db))
        self.assertEqual(live, {"features": self.index["features"], "contract_version": self.index["contract_version"],
                                "default_subcommittee": self.index["default_subcommittee"]})
        # each payload the server reads is the file the export wrote
        served = W.payloads(str(self.db))
        written = sorted(p.relative_to(self.out / "data").as_posix() for p in (self.out / "data").rglob("*.json"))
        # the export writes a large subcommittee one file per fiscal year (subcommittees/<name>/...) instead of whole:
        # put back together, each is the payload the server answers with; every other file is one such payload
        split = {p for p in written if p.count("/") == 2 and p.startswith("subcommittees/")}
        joined = {f"subcommittees/{n}.json" for n in self.index["split_subcommittees"]}
        self.assertTrue(joined)
        self.assertFalse(joined & set(written))
        self.assertEqual(sorted(served), sorted((set(written) - split) | joined))
        for path in ("subcommittees.json", "accounts/ACC-HHS-NIH-NCI.json"):
            self.assertEqual(served[path], json.loads((self.out / "data" / path).read_text()), path)
        for path in sorted(joined):
            self.assertEqual(served[path], E.read_grid(self.out / "data", path[len("subcommittees/"):-5]), path)
        unsplit = [n for n in json.loads((self.out / "data" / "subcommittees.json").read_text())["subcommittees"]
                   if n not in self.index["split_subcommittees"]]
        for n in unsplit:
            path = f"subcommittees/{n}.json"
            self.assertEqual(served[path], json.loads((self.out / "data" / path).read_text()), path)
        self.assertEqual(served["index.json"]["accounts"], self.index["accounts"])
        # and over HTTP
        httpd = W.serve(self.db, port=0, verbose=False)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        try:
            import urllib.request
            with urllib.request.urlopen(f"http://127.0.0.1:{httpd.server_address[1]}/api/features") as r:
                self.assertEqual(json.loads(r.read()), live)
        finally:
            httpd.shutdown()
            httpd.server_close()

    def test_every_feature_names_fields_that_exist_or_is_off(self):
        self.assertTrue(self.contract["features"])
        for name, f in self.contract["features"].items():
            self.assertTrue(f["fields"], f"{name} names no fields")
            for field in f["fields"]:
                self.assertRegex(field, r"^[\w*./-]+\.json:[^:\s]+$", name)
            missing = [x for x in f["fields"] if not self.present[x]]
            if missing:
                self.assertFalse(self.index["features"][name], f"{name} is on but missing {missing}")

    def test_expected_flags_today(self):
        # SPEC section 9: the figure's source is published, and since UI-0 the accounting data (contract_version 2)
        got = self.index["features"]
        self.assertTrue(got["figure_source"])
        for name in ("accounting_views", "budget_class", "defense_split"):
            self.assertTrue(got[name], name)
        # UI-0b (contract_version 4): the excluding-earmarks and nondefense views
        for name in ("exclude_earmarks", "nondefense"):
            self.assertTrue(got[name], name)
        self.assertGreaterEqual(self.index["contract_version"], 4)

    def test_a_missing_field_turns_its_feature_off(self):
        with tempfile.TemporaryDirectory() as d:
            data = Path(d)
            (data / "subcommittees").mkdir()
            (data / "subcommittees" / "X.json").write_text(json.dumps(
                {"rows": [{"attributes": {"2026": {"budget_class": None}},
                           "cells": {"2026|Enacted": {"views": {}}, "2026|Senate Reported": {"views": {"x": None}}}}]}))
            got = E.features(data, {"accounts": [{"canonical_account_id": "ACC-X", "canonical_name": "X"}]})
        self.assertFalse(got["features"]["budget_class"])          # null is not a value
        self.assertFalse(got["features"]["accounting_views"])      # nor is a view with no amount
        self.assertFalse(got["features"]["exclude_earmarks"])
        self.assertFalse(got["features"]["nondefense"])
        self.assertFalse(got["features"]["figure_source"])
        self.assertTrue(got["features"]["watchlist"])


@unittest.skipUnless(NODE, "node not installed")
class MatcherParity(StaticTest):
    """web/match.js gives approps_store.resolve()'s answer for every query."""

    def test_same_answers_as_python(self):
        # every query (14,574) on pushes to main; a pull request's CI sets PARITY_SAMPLE for a
        # fixed, seeded sample spread across the query kinds (the matcher itself is unchanged)
        typed = typed_queries(self.index["accounts"])
        self.assertGreater(len(typed), 2000)
        size = int(os.environ.get("PARITY_SAMPLE") or 0)
        queries = parity_sample(typed, size) if size else sorted(typed)
        script = f"""
            const m = require({json.dumps(str(ROOT / "web" / "match.js"))});
            const idx = require({json.dumps(str(self.out / "data" / "index.json"))});
            const qs = JSON.parse(require("fs").readFileSync(0, "utf8"));
            m.configure(idx.matching);
            const out = qs.map(q => {{
              const r = m.resolve(idx.accounts, q);
              return {{query: r.query, agency: r.agency, name: r.name, match: r.match, distance: r.distance,
                       matched_name: r.matched_name, via: r.via,
                       account: r.account ? r.account.canonical_account_id : null, candidates: r.candidates}};
            }});
            process.stdout.write(JSON.stringify(out));
        """
        # node answers while Python's answers are worked out across the CPUs (same queries, same order)
        node = subprocess.Popen([NODE, "-e", script], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        n = max(1, min(os.cpu_count() or 1, 8))
        chunks = [queries[i::n] for i in range(n)]
        with multiprocessing.get_context("spawn").Pool(n) as pool:
            parts = pool.starmap(python_answers, [(str(self.db), c) for c in chunks])
        want = [None] * len(queries)
        for i, part in enumerate(parts):
            want[i::n] = part
        out, err = node.communicate(json.dumps(queries))
        self.assertEqual(node.returncode, 0, err)
        got = json.loads(out)
        diffs = [(q, w, g) for q, w, g in zip(queries, want, got) if w != g]
        self.assertEqual(diffs[:3], [])
        kinds = {w["match"] for w in want}
        self.assertEqual(kinds, {"exact", "ocr_corrected", "ambiguous", "unmatched"})    # every outcome exercised


@unittest.skipUnless(sync_playwright and chromium_path(), "playwright / chromium not available")
class StaticVersusLive(StaticTest):
    """The static site renders exactly what the live UI renders."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        class Quiet(http.server.SimpleHTTPRequestHandler):
            def log_message(self, *a):
                pass
        handler = functools.partial(Quiet, directory=str(cls.out))
        cls.static = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        cls.live = W.serve(cls.db, port=0, verbose=False)
        for srv in (cls.static, cls.live):
            threading.Thread(target=srv.serve_forever, daemon=True).start()
        cls.static_url = f"http://127.0.0.1:{cls.static.server_address[1]}/"
        cls.live_url = f"http://127.0.0.1:{cls.live.server_address[1]}/"
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(executable_path=chromium_path())

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()
        for srv in (cls.static, cls.live):
            srv.shutdown()
            srv.server_close()
        super().tearDownClass()

    def setUp(self):
        self.page = self.browser.new_page()
        self.errors = []
        self.page.on("pageerror", lambda e: self.errors.append(str(e)))

    def tearDown(self):
        self.page.close()
        self.assertEqual(self.errors, [])

    def open_lines(self):
        """Open "Every line on file" (the record grid; collapsed by default, owner 2026-10-14) when an account is shown."""
        if self.page.is_visible("[data-testid=result]") and self.page.get_attribute("[data-testid=ap-lines]", "open") is None:
            self.page.click("[data-testid=ap-lines-toggle]")

    def shown(self, url, q, pick=None):
        self.page.goto(url + "?view=account")             # Explore is the default view; One account is beside it
        self.page.fill("#q", q)
        self.page.click("button[type=submit]")
        self.page.wait_for_selector("[data-testid=result]:not([hidden]), [data-testid=candidates]:not([hidden])")
        if pick:
            self.page.click(f"[data-testid=candidate][data-id={pick}]")
            self.page.wait_for_selector("[data-testid=result]:not([hidden])")
        self.open_lines()
        return self.page.evaluate("""() => ({
            candidates: document.querySelector('#candidates').hidden ? null : document.querySelector('#candidates').innerText,
            result: document.querySelector('#result').hidden ? null : document.querySelector('#result').innerText,
            grid: document.querySelector('#grid').innerHTML,
            message: document.querySelector('#message').innerText })""")

    def same(self, q, pick=None):
        live, static = self.shown(self.live_url, q, pick), self.shown(self.static_url, q, pick)
        self.assertEqual(static, live, q)
        return static

    def test_nasa_science(self):
        r = self.same("NASA Science")
        self.assertIn("FY2017", r["result"])
        self.assertIn("$7,250,000,000", r["result"])

    def test_former_name_exact_and_misspelled(self):
        for q in ("Deep Space Exploration Systems", "Deep Spaee Exploratlon Systems"):
            r = self.same(q)
            self.assertIn("Matched through a former name", r["result"])
        self.assertIn("2 edits away", r["result"])

    def test_leo_resolves_to_space_operations(self):
        r = self.same("LEO and Spaceflight Operations")
        self.assertTrue(r["result"].startswith("Space Operations"))

    def test_ambiguous_picker_and_pick(self):
        r = self.same("Office of Inspector General")
        self.assertIsNone(r["result"])
        for oig in ("ACC-DOJ-OIG", "ACC-NASA-OIG", "ACC-NSF-OIG"):
            self.assertIn(oig, r["candidates"])
        r = self.same("Office of Inspector General", pick="ACC-NSF-OIG")
        self.assertIn("National Science Foundation", r["result"])

    def test_three_states_and_other_queries(self):
        for q in ("NASA Exploration", "NASA Space Technology", "NSF Research and Related Activities", "NASA",
                  "Crime Victims Fund", "Scince", "Nothing Like Any Account", "NASA Education"):
            self.same(q)

    def test_static_page_says_how_fresh_it_is(self):
        self.page.goto(self.static_url + "?view=account")
        self.page.wait_for_selector("[data-testid=freshness]:not([hidden])")
        text = self.page.text_content("[data-testid=freshness]")
        self.assertIn("data/staged.json (v38)", text)
        self.assertIn("not live", text)
        # the Download workbook link opens docs/Approps_Pilot_Schema_Loaded.xlsx, the CI-built workbook
        link = self.page.locator("[data-testid=freshness] [data-testid=download-workbook]")
        self.assertEqual(link.get_attribute("href"), "Approps_Pilot_Schema_Loaded.xlsx")
        self.assertEqual(link.text_content(), "Download workbook")
        resolved = self.page.evaluate("(a) => new URL(a, location.href).href", link.get_attribute("href"))
        self.assertTrue(resolved.endswith("/Approps_Pilot_Schema_Loaded.xlsx"))
        body = self.page.request.get(resolved).body()
        self.assertEqual(body, (self.out / "Approps_Pilot_Schema_Loaded.xlsx").read_bytes())
        self.page.goto(self.live_url + "?view=account")
        self.assertTrue(self.page.locator("[data-testid=freshness]").is_hidden())
        self.assertEqual(self.page.locator("[data-testid=download-workbook]").count(), 0)


if __name__ == "__main__":
    unittest.main()
