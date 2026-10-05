<SIDECAR:extract_knowledge>
Based strictly on the preceding conversation, implementation edits, error diagnoses, and verified solutions, synthesize a reusable, permanent knowledge node capturing non-obvious engineering patterns, library quirks, or repository conventions.
Adhere strictly to the knowledge structure, guidelines, and schema defined in <KNOWLEDGE_HARVEST_SIDECAR>.

CRITICAL: If the conversation trajectory contains only routine chatter, trivial lookups, or transient edits with no reusable repository conventions, framework gotchas, or permanent rules, output ONLY:
NO_HARVEST

TARGET SCOPE: {scope}
NATURE: {nature}
TOPIC HINT: {topic_hint}

Produce the full GitHub-Flavored Markdown file including strict YAML frontmatter:
<example>
---
title: "[Concise Title]"
namespace: "{scope}"
status: "active"
nature: "{nature}"
---
</example>
</SIDECAR:extract_knowledge>
