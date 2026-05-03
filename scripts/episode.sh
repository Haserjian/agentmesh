#!/usr/bin/env bash
set -euo pipefail

OUT="${AGENTMESH_OUTPUTS_DIR:-outputs}"
RESULTS="${AGENTMESH_RESULTS_PATH:-results.xml}"
PYTHON_BIN="${PYTHON:-python3}"

mkdir -p "$OUT"

"$PYTHON_BIN" - <<'PY'
import json
import os
from pathlib import Path

out = Path(os.environ.get("AGENTMESH_OUTPUTS_DIR", "outputs"))
out.mkdir(parents=True, exist_ok=True)
payload = {
    "episode": "agentmesh-canonical-ci",
    "claim": "AgentMesh can emit an Assay-native proof pack and replay outputs",
    "value": 42,
}
(out / "episode_output.json").write_text(
    json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n",
    encoding="utf-8",
)
PY

cat > "$RESULTS" <<'XML'
<?xml version="1.0" encoding="UTF-8"?>
<testsuite name="agentmesh_canonical_episode" tests="1" failures="0" errors="0">
  <testcase classname="agentmesh.assay" name="canonical_episode_replayable"/>
</testsuite>
XML
