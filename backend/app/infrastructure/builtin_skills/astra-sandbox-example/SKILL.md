---
name: astra-sandbox-example
description: Demonstrate a Skill with a deterministic bundled script that must run through Astra's sandboxed tool pipeline.
compatibility: Requires an enabled shell sandbox tool
allowed-tools: bash_execute
metadata:
  author: Astra
  version: "1.0"
  recommended_answer_mode: trusted
  recommendation_reason: Script workflows benefit from an explicit plan and completion verification.
---

# Sandboxed example

When explicitly asked to demonstrate Skill script execution, run `scripts/hello.py` through an eligible sandboxed shell tool. Never execute it merely because this Skill was activated.
