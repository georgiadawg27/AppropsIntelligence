// node --test platform/data-worker/test/index.test.mjs  (no packages: a stand-in for the R2 binding)
import { test } from "node:test";
import assert from "node:assert/strict";
import worker from "../src/index.js";

const FILES = { "data/index.json": '{"a":1}\n', "data/subcommittees/LHHS/2026.json": '{"b":2}\n', "secret.txt": "no" };
const obj = (key, body) => ({ size: Buffer.byteLength(FILES[key]), httpEtag: `"${key.length}"`, body });
const env = {
  DATA: {
    async get(key, opts = {}) {
      if (!(key in FILES)) return null;
      const inm = opts.onlyIf && opts.onlyIf.get("if-none-match");
      return obj(key, inm === `"${key.length}"` ? undefined : FILES[key]);
    },
    async head(key) { return key in FILES ? obj(key) : null; },
  },
};
const call = (path, init) => worker.fetch(new Request("https://data.example" + path, init), env);

test("serves a data file as JSON, readable from any origin", async () => {
  const r = await call("/data/subcommittees/LHHS/2026.json");
  assert.equal(r.status, 200);
  assert.equal(await r.text(), FILES["data/subcommittees/LHHS/2026.json"]);
  assert.equal(r.headers.get("content-type"), "application/json; charset=utf-8");
  assert.equal(r.headers.get("access-control-allow-origin"), "*");
  assert.match(r.headers.get("cache-control"), /^public, max-age=\d+$/);
  assert.equal(r.headers.get("etag"), `"${"data/subcommittees/LHHS/2026.json".length}"`);
});

test("an unchanged file is a 304", async () => {
  const r = await call("/data/index.json", { headers: { "if-none-match": '"15"' } });
  assert.equal(r.status, 304);
  assert.equal(await r.text(), "");
});

test("HEAD answers without a body", async () => {
  const r = await call("/data/index.json", { method: "HEAD" });
  assert.equal(r.status, 200);
  assert.equal(r.headers.get("content-length"), String(FILES["data/index.json"].length));
});

test("only JSON under data/ is answered", async () => {
  for (const p of ["/secret.txt", "/data/../secret.txt", "/data/%2e%2e/secret.txt", "/data/", "/", "/data/missing.json",
                   "/data//index.json", "/data/%zz.json"])
    assert.equal((await call(p)).status, 404, p);
});

test("read only", async () => {
  for (const method of ["PUT", "POST", "DELETE"]) assert.equal((await call("/data/index.json", { method })).status, 405, method);
  assert.equal((await call("/data/index.json", { method: "OPTIONS" })).status, 204);
});
