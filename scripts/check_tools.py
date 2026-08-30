"""Verify every tool registers and produces a valid JSON schema."""
import json, sys
from jarvis import tools
from jarvis.tools.registry import REGISTRY

print(f"registered tools: {len(REGISTRY)}\n")
bad = []
by_risk = {}
for name, t in sorted(REGISTRY.items()):
    by_risk.setdefault(t.risk.value, []).append(name)
    s = t.schema()
    fn = s["function"]
    if not fn.get("description"):
        bad.append(f"{name}: missing description")
    for arg, spec in fn["parameters"]["properties"].items():
        if "type" not in spec and "enum" not in spec:
            bad.append(f"{name}.{arg}: no type")
    try:
        json.dumps(s)
    except Exception as e:
        bad.append(f"{name}: not serialisable: {e}")

for risk in ("safe", "moderate", "high"):
    names = by_risk.get(risk, [])
    print(f"{risk.upper():<9} ({len(names):>2}): {', '.join(names)}\n")

if bad:
    print("PROBLEMS:"); [print("  -", b) for b in bad]; sys.exit(1)
print("all schemas valid")
