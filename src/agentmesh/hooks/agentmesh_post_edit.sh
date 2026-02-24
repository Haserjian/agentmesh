#!/usr/bin/env bash
# AgentMesh PostToolUse hook for Claude Code
# Sends heartbeat after successful edit operations.

set -euo pipefail

AGENT_ID="${AGENTMESH_AGENT_ID:-claude_$(basename "${TTY:-notty}")_$$}"

agentmesh heartbeat --agent "$AGENT_ID" --status busy >/dev/null 2>&1 || true
exit 0
