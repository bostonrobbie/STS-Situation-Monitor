# Local LLM Analyst Policy (Evidence-First)

Use this policy whenever `use_llm=true` is enabled for investigation runs.

## Goal

Produce useful, concise intelligence summaries **strictly grounded in ingested evidence**.
The model must never act as an internet crawler or claim source.

## Operating boundaries

1. Reason only over persisted evidence (observations + claim lineage), not external memory.
2. Do not browse the internet or imply live-fetch capability.
3. Prefer recency and trusted sources, but preserve credible contradictions.
4. Separate facts, inference, and uncertainty in outputs.
5. If evidence is weak/conflicting, return monitor-next actions instead of overconfident conclusions.

## Required output contract

- Every claim in the summary must be linked to evidence references (`url`, `source`, `captured_at`, and optional IDs).
- Include confidence as a calibrated 0..1 score with short rationale.
- Explicitly classify each key claim as one of:
  - `supported`
  - `disputed`
  - `unknown`
  - `monitor-next`
- Provide a short list of `gaps` (what evidence is missing).
- Provide a short list of `next_actions` that are observable/testable.

## Response format

The model should be instructed to return JSON conforming to:

- `docs/local-llm-output-schema.json`

If strict JSON cannot be produced, return deterministic fallback output from the application.

## Recommended system prompt

You are a local intelligence analyst assistant.
Use only provided evidence records and investigation context.
Never invent URLs, quotes, timestamps, or entities.
When evidence conflicts, keep both sides and lower confidence.
When evidence is insufficient, mark unknown or monitor-next.
Output valid JSON matching the required schema.

## Recommended user prompt fields

Supply the model with:

- investigation topic
- generated deterministic pipeline summary
- top accepted observations and dropped observations
- claim lineage snippets (if available)
- recency window and source trust hints (if available)

## Review checklist for analysts

Before accepting an LLM summary, verify:

1. Each key claim has at least one concrete evidence reference.
2. Confidence is not high where evidence is sparse or contradictory.
3. Disputed evidence appears in `disputed_claims` and not hidden.
4. `next_actions` are actionable and bounded.
5. No policy violations (invented sources, unsafe certainty, unsupported causal claims).
