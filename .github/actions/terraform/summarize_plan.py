"""Summarise a `terraform show -json` plan WITHOUT printing any attribute value (these logs are public).

Writes resource addresses and actions to the job summary and sets the outputs `changes` and `destructive`
(destructive = anything is deleted or replaced).
"""

from __future__ import annotations

import json
import os
import sys
from collections import Counter

plan = json.load(open(sys.argv[1]))
rows = []
counts: Counter[str] = Counter()
destructive = False
for rc in plan.get("resource_changes", []):
    actions = rc["change"]["actions"]
    if actions in (["no-op"], ["read"]):
        continue
    kind = "replace" if "delete" in actions and "create" in actions else actions[0]
    if "delete" in actions:
        destructive = True
    counts[kind] += 1
    rows.append((kind, rc["address"]))

lines = ["### Terraform plan", ""]
if not rows:
    lines.append("No changes. Infrastructure matches the configuration.")
else:
    lines.append("Plan: " + ", ".join(f"{n} to {k}" for k, n in sorted(counts.items())) + ".")
    if destructive:
        lines.append("")
        lines.append(
            "**This plan destroys or replaces resources**, so it needs the owner's approval (`infra-apply-destructive`)."
        )
    lines += ["", "| Action | Resource |", "|---|---|"] + [f"| {k} | `{a}` |" for k, a in sorted(rows)]

text = "\n".join(lines) + "\n"
print(text)
with open(os.environ.get("GITHUB_STEP_SUMMARY", os.devnull), "a") as out:
    out.write(text)
with open(os.environ.get("GITHUB_OUTPUT", os.devnull), "a") as out:
    out.write(f"changes={'true' if rows else 'false'}\n")
    out.write(f"destructive={'true' if destructive else 'false'}\n")
