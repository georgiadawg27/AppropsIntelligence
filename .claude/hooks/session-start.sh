#!/bin/bash
# SessionStart hook for Claude Code on the web.
#
# 1. Writes the project .env from the cloud environment's variables. The
#    environment settings don't pass ANTHROPIC_API_KEY through (Claude Code
#    reserves that name for its own auth), so the Anthropic key is stored
#    there as APPROPS_ANTHROPIC_API_KEY and written to .env under the name
#    the scripts read. Key values are never printed.
# 2. Installs Python dependencies.
# 3. Fetches the committee reports the tests and acceptance runs use, if missing.
set -euo pipefail

if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

cd "$CLAUDE_PROJECT_DIR"

python3 - <<'PY'
import os
from pathlib import Path

env_path = Path(".env")
wanted = {
    "ANTHROPIC_API_KEY": os.environ.get("APPROPS_ANTHROPIC_API_KEY", ""),
    "GOVINFO_API_KEY": os.environ.get("GOVINFO_API_KEY", ""),
}
lines = env_path.read_text().splitlines() if env_path.exists() else []
written = []
for name, value in wanted.items():
    if not value:
        continue
    lines = [l for l in lines if not l.startswith(f"{name}=")]
    lines.append(f"{name}={value}")
    written.append(name)
if written:
    env_path.write_text("\n".join(lines) + "\n")
    env_path.chmod(0o600)
missing = [n for n, v in wanted.items() if not v]
print(f"session-start: .env has {', '.join(written) or 'nothing new'}"
      + (f"; not provided by the environment: {', '.join(missing)}" if missing else ""))
PY

pip install --quiet --disable-pip-version-check -r requirements.txt 2>&1 | grep -v "Running pip as the 'root' user" || true

# Committee reports the tests read, fetched through govinfo_ingest's own
# fetch_and_store (not a hand-built URL) so the ingest path is what's exercised.
python3 - <<'PY' || echo "session-start: report fetch failed (continuing)"
import os
import govinfo_ingest as g

key = os.environ.get("GOVINFO_API_KEY")
reports = ["CRPT-119hrpt652", "CRPT-119srpt44", "CRPT-118srpt62"]
missing = [r for r in reports if not (g.STORE_DIR / f"{r}.pdf").exists()]
if missing and key:
    manifest = g.load_manifest()
    for pid in missing:
        try:
            print(f"session-start: {pid}: {g.fetch_and_store(pid, key, manifest)['status']}")
        except Exception as e:
            print(f"session-start: {pid}: fetch failed ({e})")
    g.save_manifest(manifest)
PY
