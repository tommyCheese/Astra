## 1. Runtime Policy Loading

- [x] 1.1 Load the persisted effective reasoning policy at Agent Loop startup
- [x] 1.2 Derive turn, tool, reflection, and replan limits from user policy under deployment hard caps

## 2. Planning Strategy Enforcement

- [x] 2.1 Keep direct strategy on a local single-step plan
- [x] 2.2 Give adaptive strategy a distinct lightweight model-contract path without full preplanning
- [x] 2.3 Preserve full model contract and planning for plan-first and plan-only modes

## 3. Reflection Enforcement

- [x] 3.1 Route model-output and tool failures through ReflectionGate
- [x] 3.2 Enforce reflection disabled, failure-only, adaptive, and every-turn behavior
- [x] 3.3 Enforce reflection budget and record skipped-reflection events

## 4. Behavioral Verification

- [x] 4.1 Add tests for fast, balanced, and deep runtime limits
- [x] 4.2 Add tests for distinct planning strategy paths
- [x] 4.3 Add tests for reflection switch, triggers, and budget
- [x] 4.4 Run backend and frontend regression suites
