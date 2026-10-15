// The public data Worker (platform plan, Phase 1): serves the site data the export workflow uploads to the R2 bucket
// (.github/workflows/export-static.yml, "Upload the site data to R2") exactly as docs/data/ serves it today, to
// anyone, read only. GET or HEAD /data/<path>.json -> the object at key data/<path>.json. Nothing else is answered.
// Phase 1b puts the login and subscription check in front of the same bucket (platform/worker/, not this one).

const DATA_KEY = /^data\/(?:[A-Za-z0-9_-]+\/)*[A-Za-z0-9_.-]+\.json$/;   // no "..", no empty segment, JSON only

// the page is served from another origin (GitHub Pages) and only reads, so any origin may read
const CORS = {
  "access-control-allow-origin": "*",
  "access-control-allow-methods": "GET, HEAD, OPTIONS",
  "access-control-max-age": "86400",
};

function text(status, body, extra = {}) {
  return new Response(body, { status, headers: { "content-type": "text/plain; charset=utf-8", ...CORS, ...extra } });
}

function withLength(headers, obj) {
  headers.set("content-length", String(obj.size));
  return headers;
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (request.method === "OPTIONS") return new Response(null, { status: 204, headers: CORS });
    if (request.method !== "GET" && request.method !== "HEAD") return text(405, "Method not allowed\n", { allow: "GET, HEAD, OPTIONS" });
    let key;
    try { key = decodeURIComponent(url.pathname.slice(1)); } catch { return text(404, "Not found\n"); }
    if (!DATA_KEY.test(key) || key.split("/").includes("..")) return text(404, "Not found\n");
    // If-None-Match only: an unchanged file is a 304 with no body
    const inm = request.headers.get("if-none-match");
    const obj = request.method === "HEAD" ? await env.DATA.head(key)
      : await env.DATA.get(key, inm ? { onlyIf: new Headers({ "if-none-match": inm }) } : {});
    if (obj === null) return text(404, "Not found\n");
    const headers = new Headers(CORS);
    headers.set("content-type", "application/json; charset=utf-8");
    // the export runs on every data change: a short cache, revalidated by ETag
    headers.set("cache-control", "public, max-age=300");
    headers.set("etag", obj.httpEtag);
    if (request.method === "HEAD") return new Response(null, { status: 200, headers: withLength(headers, obj) });
    if (!obj.body) return new Response(null, { status: 304, headers });
    return new Response(obj.body, { status: 200, headers: withLength(headers, obj) });
  },
};
