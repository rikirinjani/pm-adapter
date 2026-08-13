#!/usr/bin/env python3
"""Test dispatch_worker() with one real task."""
import sys
sys.path.insert(0, r"C:\Users\think\Project\pm-adapter")

from pm_adapter.dispatch import dispatch_worker

result = dispatch_worker(
    project="morse",
    task_spec="Check if PLANS.md Task 8 is correctly marked. The paper already has section 2.4 about hybrid encoding.",
    agent="fixer",
)

print(f"Status: {result.status}")
print(f"Content length: {result.content_length}")
print(f"Duration: {result.duration:.1f}s")
print(f"Events recorded: {result.events_recorded}")
print(f"Error: {result.error or 'none'}")
if result.content:
    print(f"Content preview: {result.content[:300]}")
