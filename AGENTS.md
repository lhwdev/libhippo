# AGENTS.md for LibHippo

In most time, read `architecture.md` first.

## Environment

- Uses `uv` package manager.

## Rules

- Use `rg` (ripgrep) rather than `grep`.
- Be conscise, try to reduce:
  * rarely used English words,
  * unnecessary explaination,
  * comments that can be figured out right from code.
- When adding code/text to file, follow style of the file.
- Preserve previous file if unnecessary.
- When refactoring internal codes, less consider backward compatibility;
  try to refactor use cases.
- Search for web when dealing with latest things, i.e. OpenAI models, API.
  * Do not mention legacy OpenAI models like o1, o4.
  * Do not spam python commands testing library APIs; search docs or read source.

### Rules for Python

- Put import on top, unless dynamic import is needed.

### Planning Rules

- Do not overuse code blocks for what will be written on each file.
- Maintain proper abstraction level to code.

## Triggers

- Use `typesafe-ai` skill for `TypeSafe Jev` related code.

## Files

- When adding key modifications to architecture, modify corresponding markdown docs.
