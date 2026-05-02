# Prompt Optimization Cheat Sheet

## Core Principle
- A longer, more detailed prompt often reduces output length and retries, leading to **lower total cost**.

## Seven Essential Prompt Blocks

| Block        | Purpose                         | Effect on Output |
|--------------|---------------------------------|------------------|
| 👤 Role       | Who is answering?               | -10%             |
| 📦 Context    | Project environment, stack      | -15%             |
| ⚡ Task       | Precise, specific request       | -30%             |
| 🚫 Constraints| What **not** to do              | -17%             |
| 📎 Example    | Short sample of desired output  | -15%             |
| ✓ Self-check | Verification steps              | -5%              |

> Adding these blocks increases input tokens slightly but drastically reduces output tokens, cutting cost.

## 2 Levers to Reduce Output Length

1. **Disambiguation** – Be precise; use function signatures, avoid ambiguous words → up to -37%.
2. **Example** – Provide a **short** example → -15% (long examples increase output).

Combine all five for up to **-73% output tokens**.

## Prompt Quality Score (PQS)
Estimate before sending: `PQS = 0.20×SCS + 0.25×DI + 0.15×II + 0.25×CSI + 0.15×SCoS`.  
Target **PQS ≥ 0.85** to minimize retries (probability of retry ≈ 1‑PQS).  
High PQS also reduces output length.

## System Prompt Tips
- Write in **English** (2× fewer tokens than Russian).  
- Use abbreviations: `Sr Dev`, `stdlib`, `No:` etc.  
- Include constant parts: role, stack, output format, global constraints.

## User Prompt Tips
- Be **specific** – provide exact function signatures, expected behavior.  
- Add **short example**.  
- Include **constraints** relevant to the task.  
- Optionally ask for **self‑check**.

A well‑optimized prompt often costs **10× less** than a vague one.

## Quick Checklist
- [ ] Use precise, unambiguous language.
- [ ] Include a short example.
- [ ] List important constraints.
- [ ] Aim for PQS ≥ 0.85.
