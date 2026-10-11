<SIDECAR:extract_knowledge>
TARGET SCOPE: {scope}
TOPIC HINT: {topic_hint}

<tools:harvest_guidance>
## Knowledge Drafting & Harvesting
Synthesize reusable, permanent knowledge document (conventions, library quirks, or architecture rules) from the conversation. All knowledge documents MUST be written entirely in formal English.
- If the conversation contains no reusable conventions or rules, output ONLY:
NO_HARVEST

- Otherwise, use following tools to draft knowledge document and submit:
- `write_knowledge(path, content, start_line?, end_line?, target?)`: Create or surgically edit draft nodes (e.g. `project/...`, `common/...`). Knowledge paths must not include `.md`. Target 500–1,000 tokens.
  - Prefer `target` (unique substring replacement) for surgical updates; it is immune to line-number shifts.
  - When modifying by `start_line` / `end_line`, ALWAYS inspect lines with `read_knowledge` first to confirm line numbers.
  - When `version_check` is needed, use canonical package detectors: `npm:<pkg>`, `pypi:<pkg>`, `github:<owner>/<repo>`, `crates:<crate>`, `scrape:<url>#<regex>`, or `terminal:<cmd>`.
- `read_knowledge(path, start_line=1, end_line?)`: Read line-addressed slices of existing knowledge or session drafts.
- `list_knowledge(path=".", max_depth=2)`: Browse existing knowledge directory hierarchies.
- `search_knowledge(path, pattern="*", content_pattern?)`: Fast lexical or regex search across existing knowledge nodes.
- `commit(path)`: Validate and submit a specific draft.
- `commit_all()`: Validate and submit all open session drafts at once.
</tools:harvest_guidance>
</SIDECAR:extract_knowledge>
