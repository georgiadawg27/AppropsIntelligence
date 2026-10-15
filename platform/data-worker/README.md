# The public data Worker (platform plan, Phase 1)

Serves the site data (`docs/data/`) from a Cloudflare R2 bucket, to anyone, exactly as GitHub Pages serves it
today. It moves the built data out of git; it changes nothing about who can read it. The login and subscription
check come later (Phase 1b, `platform/worker/`), in front of the same bucket.

- `GET` / `HEAD /data/<path>.json` answers with the object at key `data/<path>.json` (JSON, `ETag`, a 5-minute
  public cache, `Access-Control-Allow-Origin: *` because the page is on another origin). Anything else is a 404,
  and only `GET`, `HEAD` and `OPTIONS` are answered.
- `.github/workflows/export-static.yml` uploads `docs/data/` to the bucket's `data/` on every export, once the
  R2 secrets exist. Nothing in CI deploys this Worker.
- Tests: `node --test platform/data-worker/test/index.test.mjs` (no packages; the tests job runs it).

## Turning it on (owner)

Each step can be checked before the next, and nothing public changes until step 5.

1. Create the Cloudflare account and an R2 bucket named `approps-data` (another name: set the repository
   variable `R2_BUCKET` to it and change `bucket_name` in `wrangler.toml`). Leave the bucket's public access off;
   the Worker reads it through its binding.
2. Create an R2 API token with Object Read & Write on that bucket only. Add the repository secrets
   `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID` and `R2_SECRET_ACCESS_KEY`.
3. Run the "Export static site" workflow by hand (or wait for the next data change). It now also uploads
   `docs/data/` to the bucket; `docs/data/` is still committed and the page still reads it.
4. From this folder: `npx wrangler login`, then `npx wrangler deploy`. Note the address it prints
   (`https://approps-data.<subdomain>.workers.dev`) and open `<address>/data/index.json` to compare it with
   `https://<the site>/data/index.json`.
5. Set the repository variable `DATA_BASE_URL` to `<address>/data/` (with the trailing slash) and run the export
   again. The page now reads the data from the Worker; before committing, the job checks the Worker answers with
   the `index.json` it just uploaded. From then on `docs/data/` is no longer committed.

To go back: delete the `DATA_BASE_URL` variable and run the export; the page reads `data/` next to it again.
Removing the old `docs/data/` from git is a separate change, made only once the owner says so.
