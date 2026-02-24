#!/usr/bin/env bash
# AgentMesh PostToolUse hook for Claude Code
# Sends heartbeat after successful edit operations.

set -euo pipefail

# Determine agent ID (session-stable: no PID)
if [ -n "${AGENTMESH_AGENT_ID:-}" ]; then
    AGENT_ID="$AGENTMESH_AGENT_ID"
elif [ -n "${TTY:-}" ]; then
    AGENT_ID="claude_$(basename "$TTY")"
elif tty -s 2>/dev/null; then
    AGENT_ID="claude_$(basename "$(tty)")"
else
    AGENT_ID="claude_notty"
fi

agentmesh heartbeat --agent "$AGENT_ID" --status busy >/dev/null 2>&1 || true
exit 0
