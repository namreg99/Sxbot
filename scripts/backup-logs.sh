#!/usr/bin/env bash
# Copy append-only trading history to the persistent agent store every 15 min.
# Paper books were lost once (Aug 21-24 unique card) when a jsonl was erased.
set -u
STORE="${1:-/cursor/stores/self/backups}"
mkdir -p "$STORE"
while true; do
    cp -f /workspace/sxbot-paper*.jsonl "$STORE"/ 2>/dev/null
    cp -f /workspace/sxbot-flow.jsonl "$STORE"/ 2>/dev/null
    cp -f /workspace/sxbot-closes.jsonl "$STORE"/ 2>/dev/null
    date -u +"backed up %Y-%m-%dT%H:%M:%SZ"
    sleep 900
done
