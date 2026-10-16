"""
The canonical data, stored split: data/staged/ holds one file per subcommittee and record type, plus a manifest.

    data/staged/manifest.json             the record types in order, and for each the runs that put its records
                                          back in their order: [[group, count], ...]
    data/staged/<GROUP>/<table>.json      one record type's records of one subcommittee (CJS, LHHS, ...), in order
    data/staged/shared/<table>.json       records of no one subcommittee (source documents, the component
                                          vocabulary, the retired-ID lists, a record whose account is not on file)

load() gives back the one document the pipeline has always used (data/staged.json's shape: {table: [records]},
the same tables and records in the same order), and save(data) writes it split; load(save(d)) == d. Nothing
outside this module reads or writes the files. One file per subcommittee keeps each well under GitHub's 100 MB file
limit as subcommittees are added (backfill readiness B2), and a change to one subcommittee touches only its files.

digest(data) is the sha256 of the document as the single file stored it (json.dumps(data, indent=1,
ensure_ascii=False)): the data's version stamp (docs/data/index.json, reference/accounts.json), unchanged by the
split. load_git(rev) reads a commit's data, split or (before the split) data/staged.json.

A record's subcommittee: its own `subcommittee`, else its account's (canonical_account_id, from_account_id), else
its observation's account's (observation_id). The manifest's counts must match the files exactly: a file edited by
hand, or a merge that kept one side's manifest, stops load() with what differs (rewrite with save()).
"""

import hashlib
import json
import subprocess
from pathlib import Path

import paths

SHARED = "shared"
MANIFEST = "manifest.json"
FORMAT = 1
NOTE = ("data/staged/: the canonical data, one file per subcommittee and record type (staged_data.py). The runs put "
        "each record type's records back in order; read and write it only with staged_data.load() and save().")


def directory(root=None):
    """data/staged/ under a data root (default paths.data_root())."""
    return (Path(root) if root is not None else paths.data_root()) / "data" / "staged"


def dumps(data):
    """The document as the single data/staged.json stored it."""
    return json.dumps(data, indent=1, ensure_ascii=False)


def digest(data):
    return hashlib.sha256(dumps(data).encode()).hexdigest()


def _groups(data):
    """record -> group (its subcommittee, or SHARED)."""
    acc = {}
    for a in data.get("accounts") or []:
        if isinstance(a, dict) and a.get("canonical_account_id") and a.get("subcommittee"):
            acc[a["canonical_account_id"]] = a["subcommittee"]
    obs = {}
    for o in data.get("observations") or []:
        if isinstance(o, dict) and o.get("observation_id") and o.get("canonical_account_id") in acc:
            obs[o["observation_id"]] = acc[o["canonical_account_id"]]

    def group(r):
        if not isinstance(r, dict):
            return SHARED
        if r.get("subcommittee"):
            return r["subcommittee"]
        for k in ("canonical_account_id", "from_account_id"):
            if r.get(k) in acc:
                return acc[r[k]]
        if r.get("observation_id") in obs:
            return obs[r["observation_id"]]
        return SHARED
    return group


def _check_group(g):
    if not isinstance(g, str) or not g or g in (".", "..") or "/" in g or "\\" in g:
        raise ValueError(f"staged_data: {g!r} cannot name a data/staged/ folder")
    return g


def split(data):
    """-> (manifest, {(group, table): [records]})"""
    if not isinstance(data, dict):
        raise ValueError("staged_data: the data is a document of tables ({table: [records]})")
    group = _groups(data)
    tables, files = {}, {}
    for table, records in data.items():
        if not isinstance(records, list):
            raise ValueError(f"staged_data: {table} is not a list of records")
        runs = []
        for r in records:
            g = _check_group(group(r))
            files.setdefault((g, table), []).append(r)
            if runs and runs[-1][0] == g:
                runs[-1][1] += 1
            else:
                runs.append([g, 1])
        tables[table] = runs
    return {"note": NOTE, "format": FORMAT, "tables": tables}, files


def _join(manifest, read):
    """The document from a manifest and read(group, table) -> [records]."""
    if manifest.get("format") != FORMAT:
        raise ValueError(f"data/staged/{MANIFEST}: format {manifest.get('format')!r}, this code reads {FORMAT}")
    data, problems = {}, []
    for table, runs in manifest["tables"].items():
        want = {}
        for g, n in runs:
            want[g] = want.get(g, 0) + n
        got = {g: read(g, table) for g in want}
        for g, n in want.items():
            if len(got[g]) != n:
                problems.append(f"{g}/{table}.json holds {len(got[g])} records, the manifest {n}")
        if problems:
            continue
        pos = dict.fromkeys(want, 0)
        out = []
        for g, n in runs:
            out.extend(got[g][pos[g]:pos[g] + n])
            pos[g] += n
        data[table] = out
    if problems:
        raise ValueError("data/staged/ does not match its manifest (write it with staged_data.save()): "
                         + "; ".join(problems))
    return data


def load(root=None):
    """The data (data/staged.json's shape) from data/staged/ under a data root (default paths.data_root())."""
    d = directory(root)
    if not (d / MANIFEST).exists():
        raise FileNotFoundError(f"{d / MANIFEST} not found: the data is data/staged/ (staged_data.py)")
    manifest = json.loads((d / MANIFEST).read_text())
    known = {g for runs in manifest["tables"].values() for g, _ in runs}
    stray = sorted(str(p.relative_to(d)) for p in d.glob("*/*.json")
                   if p.parent.name not in known or p.stem not in manifest["tables"]
                   or p.parent.name not in {g for g, _ in manifest["tables"][p.stem]})
    if stray:
        raise ValueError(f"data/staged/ has files its manifest does not list: {', '.join(stray)}")
    return _join(manifest, lambda g, t: json.loads((d / _check_group(g) / f"{t}.json").read_text()))


def load_tree(root):
    """The data of a checkout (a base branch's worktree): data/staged/, or data/staged.json from before the split."""
    if (directory(root) / MANIFEST).exists():
        return load(root)
    return json.loads((Path(root) / "data" / "staged.json").read_text())


def save(data, root=None):
    """Write the data to data/staged/ under a data root (default paths.data_root()): every file whose content
    changed, and the manifest; files of a group and record type that no longer has records are removed."""
    d = directory(root)
    manifest, files = split(data)
    for (g, table), records in files.items():
        p = d / g / f"{table}.json"
        text = dumps(records) + "\n"
        if not p.exists() or p.read_text() != text:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text)
    for p in d.glob("*/*.json"):
        if (p.parent.name, p.stem) not in files:
            p.unlink()
    for p in d.iterdir() if d.exists() else ():
        if p.is_dir() and not any(p.iterdir()):
            p.rmdir()
    (d / MANIFEST).write_text(_manifest_text(manifest))
    return d


def _manifest_text(manifest):
    """The manifest with one line per record type (its runs), so a change reads as one line in a diff."""
    head = {k: v for k, v in manifest.items() if k != "tables"}
    lines = [f" {json.dumps(k)}: {json.dumps(v, ensure_ascii=False)}," for k, v in head.items()]
    rows = [f"  {json.dumps(t)}: {json.dumps(runs)}" for t, runs in manifest["tables"].items()]
    return "{\n" + "\n".join(lines) + '\n "tables": {\n' + ",\n".join(rows) + "\n }\n}\n"


def mtime(root=None):
    """The newest modification time among data/staged/'s files (a build is current when newer)."""
    d = directory(root)
    return max(p.stat().st_mtime for p in [d / MANIFEST, *d.glob("*/*.json")])


def copy(dest_root, root=None):
    """Copy data/staged/ under another root (a scratch build folder)."""
    import shutil
    shutil.copytree(directory(root), directory(dest_root))


def _git_show(cwd, rev, path):
    r = subprocess.run(["git", "show", f"{rev}:{path}"], cwd=cwd, capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else None


def load_git(rev, cwd=None):
    """A commit's data: data/staged/ (split), or data/staged.json before the split; None where it has neither (or
    the ref is not fetched)."""
    cwd = cwd or paths.ROOT
    m = _git_show(cwd, rev, f"data/staged/{MANIFEST}")
    if m is not None:
        return _join(json.loads(m), lambda g, t: json.loads(_git_show(cwd, rev, f"data/staged/{g}/{t}.json")))
    old = _git_show(cwd, rev, "data/staged.json")
    return json.loads(old) if old is not None else None


# git log paths for the data: the folder, and the single file it replaced
GIT_PATHS = ("data/staged", "data/staged.json")


def _main(argv=None):
    """python staged_data.py --digest | --check  (print the data's digest | load and check the round trip)"""
    import argparse
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--digest", action="store_true", help="print the data's sha256 (digest())")
    p.add_argument("--check", action="store_true", help="load, split again and check nothing would change")
    a = p.parse_args(argv)
    data = load()
    if a.digest:
        print(digest(data))
    if a.check:
        manifest, files = split(data)
        if _join(manifest, lambda g, t: files.get((g, t), [])) != data:
            print("data/staged/: the split does not give the data back")
            return 1
        print(f"data/staged/: {len(files)} files, {sum(len(v) for v in files.values())} records, round trip ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
