# AGENTS.md for LibHippo

In most time, read `architecture.md` first.

## Environment

- Uses `uv` package manager.

## Rules

- Use `rg` (ripgrep) rather than `grep`.
- Be conscise, try to reduce:
  * unnecessary explaination,
  * comments that can be figured out right from code.
- When adding code/text to file, follow style of the file.
- Preserve previous file if unnecessary.
- When refactoring internal codes, less consider backward compatibility;
  try to refactor use cases.

## Planning Rules

- Do not overuse code blocks for what will be written on each file.
- Maintain proper abstraction level to code.

## Triggers

- Use `typesafe-ai` skill for `TypeSafe Jev` related code.

## Files

- When adding key modifications to architecture, modify `architecture.md` and also append to
  corresponding section in `design_rationale_and_qa.md`.
