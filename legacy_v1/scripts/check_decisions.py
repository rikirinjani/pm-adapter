#!/usr/bin/env python3
"""B4: Validate real decision data quality."""
import json
from pathlib import Path

ds_dir = Path.home() / '.config/opencode/pm1-decisions'

if not ds_dir.exists():
    print('No pm1-decisions directory found')
    exit(1)

for ds_path in sorted(ds_dir.glob('*.jsonl')):
    print(f'\n=== {ds_path.name} ===')
    with open(ds_path) as f:
        lines = [json.loads(l) for l in f if l.strip()]

    print(f'Total decisions: {len(lines)}')
    print()

    # Group by status
    active = [d for d in lines if d.get('status') == 'active']
    superseded = [d for d in lines if d.get('status') == 'superseded']
    contradictions = [d for d in lines if d.get('status') == 'contradiction']

    print(f'Active: {len(active)}')
    print(f'Superseded: {len(superseded)}')
    print(f'Contradictions: {len(contradictions)}')
    print()

    # Show all with quality checks
    issues = []
    for d in lines:
        did = d.get('id', '?')
        dec = d.get('decision', '?')
        status = d.get('status', '?')
        tags = d.get('tags', [])
        domain = d.get('domain', '?')
        rationale = d.get('rationale', '?')
        source = d.get('source', '?')
        superseded_by = d.get('superseded_by', None)

        print(f'[{status}] {did[:8]}  domain={domain}  tags={tags}')
        print(f'  decision: {dec[:100]}')
        print(f'  rationale: {rationale[:100]}')
        print(f'  source: {source}')
        if superseded_by:
            print(f'  superseded_by: {superseded_by[:8]}')
        print()

        # Quality checks
        if not tags:
            issues.append(f'{did[:8]}: no tags')
        if domain == 'unknown':
            issues.append(f'{did[:8]}: domain=unknown')
        if len(rationale) < 10:
            issues.append(f'{did[:8]}: rationale too short ({len(rationale)} chars)')
        if 'tool_call' not in source and 'api_call' not in source and source != 'manual':
            issues.append(f'{did[:8]}: unusual source={source}')

    if issues:
        print(f'ISSUES ({len(issues)}):')
        for i in issues:
            print(f'  - {i}')
    else:
        print('No quality issues found.')
