#!/usr/bin/env python3
"""Analyze dispatch event log."""
import sys, io, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

log_file = r'C:\Users\think\.config\opencode\pm1-logs\morse.jsonl'
with open(log_file, encoding='utf-8') as f:
    lines = [json.loads(l) for l in f if l.strip()]

handoffs = [l for l in lines if l['schema'] == 'handoff']
calls = [l for l in lines if l['schema'] == 'tool_call']

print(f'Total events: {len(lines)}')
print(f'Handoffs: {len(handoffs)}')
print(f'Tool calls: {len(calls)}')
completed = sum(1 for c in calls if c['meta']['outcome'] == 'completed')
empty = sum(1 for c in calls if c['meta']['outcome'] == 'empty')
errors = sum(1 for c in calls if c['meta']['outcome'] == 'error')
print(f'Completed: {completed}')
print(f'Empty: {empty}')
print(f'Errors: {errors}')
print()
for h in handoffs:
    meta = h['meta']
    print(f'Handoff: {meta["task"][:60]}... -> {meta["to"]}')
