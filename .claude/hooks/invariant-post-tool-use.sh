#!/usr/bin/env bash
# Installed by invariant. This file is a shim on purpose: all logic lives in
# the library, so upgrading it never means merging changes back into here.
# Project-specific behaviour belongs in .claude/invariant.yaml, not in this file.
set -euo pipefail

# Resolution order. Every candidate is on PATH or expressed relative to $HOME
# or the project — never an absolute path carrying a username, because this
# file is committed.
for candidate in \
  "$(command -v invariant 2>/dev/null || true)" \
  "$HOME/.local/bin/invariant" \
  "${CLAUDE_PROJECT_DIR:-.}/.claude/invariant/bin/invariant"
do
  if [ -n "$candidate" ] && [ -x "$candidate" ]; then
    exec "$candidate" hook post-tool-use
  fi
done

# Not installed. Staying silent is correct: a missing tool must not break every
# tool call in the project.
echo '{}'
