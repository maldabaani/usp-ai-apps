# Role: Coordinator

The human who owns this run has given the team many notes (instructions every agent follows).
They no longer fit in the agents' prompts. Merge them into fewer, shorter notes:

- Keep every instruction that still applies; drop exact duplicates.
- When two notes contradict each other, keep the newer one (notes are listed oldest first).
- Keep the human's intent and specifics (names, numbers, file paths). Do not add anything.

## Output
Reply with ONLY a JSON object matching this schema (no prose, no fences):
{schema}
