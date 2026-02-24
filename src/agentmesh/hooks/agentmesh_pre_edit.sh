#!/usr/bin/env bash
# AgentMesh PreToolUse hook for Claude Code
# Checks for file claim conflicts before Edit/Write operations.
# Stdin: JSON with tool_input.file_path
# Stdout: JSON with hookSpecificOutput if conflict detected

set -euo pipefail

# Read hook input from stdin
INPUT=$(cat)

# Extract file_path from tool_input
FILE_PATH=$(echo "$INPUT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('tool_input',{}).get('file_path',''))" 2>/dev/null || true)

if [ -z "$FILE_PATH" ]; then
    exit 0
fi

# Determine agent ID
AGENT_ID="${AGENTMESH_AGENT_ID:-claude_$(basename "${TTY:-notty}")_$$}"

# Check for conflicts (excluding self)
CONFLICTS=$(agentmesh check "$FILE_PATH" --agent "$AGENT_ID" --json 2>/dev/null || echo "[]")

if [ "$CONFLICTS" != "[]" ]; then
    # Extract first conflicting agent
    OWNER=$(echo "$CONFLICTS" | python3 -c "import sys,json; c=json.load(sys.stdin); print(c[0]['agent_id'] if c else 'unknown')" 2>/dev/null || echo "unknown")
    # Signal conflict to Claude Code
    cat <<HOOK_EOF
{"hookSpecificOutput": {"permissionDecision": "ask", "permissionDecisionReason": "AgentMesh: File claimed by $OWNER. Proceed?"}}
HOOK_EOF
    exit 0
fi

# No conflict -- auto-claim with 30min TTL
agentmesh claim "$FILE_PATH" --agent "$AGENT_ID" --ttl 1800 >/dev/null 2>&1 || true
exit 0
