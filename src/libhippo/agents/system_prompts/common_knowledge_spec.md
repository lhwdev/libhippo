<knowledge:spec>
# Knowledge Document Specification

Every LibHippo knowledge is uniquely identified by path; content must be valid GitHub-Flavored Markdown with YAML frontmatter.

## Knowledge Path Convention
- Starts with namespace: `common/`, `project/`, `user/`, `plugins/`.
- Every path segment represents unique knowledge document; both `common`, `common/web` represents one document.
- Must strictly follow lowercase `snake_case`: `common/web/react`, `project/agent_runtime`.
- Represents canonical technical identifiers and concepts. NEVER directly mirror queries or include generic query filler like `specification`, `usage`, `guide`, `overview`, or `tutorial`.
- Parent knowledge MUST exist: when writing `common/web/react/use_effect`, check for existence of `common/web/react` first.

## Frontmatter
YAML frontmatter contains following properties:

<knowledge:spec_frontmatter>
title: string // required: concise, unambiguous topic title
version?: string // optional: target library/technology version (defaults to "1.0.0")
source?: UrlString[] // recommended: authoritative canonical documentation URLs
version_check?: `${VersionCheckSource}:${UrlString}` // optional: package/command version detector
tags?: string[] // optional: semantic keyword tags
related?: KnowledgePath[] // optional: related knowledge document paths
</knowledge:spec_frontmatter>

<knowledge:example>
---
title: "React"
version: "19.3.0"
source:
  - "https://react.dev/"
  - "https://react.dev/learn"
version_check: "npm:react"
tags: ["library", "javascript", "ui", "declarative"]
related:
  - "common/web/html"
---

React is a JavaScript library for building user interfaces (UIs), especially interactive web applications.

React applications follow functional component architecture with unidirectional data flow and immutable state updates.

## Core Architecture
- **Pure Rendering**: Components must be pure functions of props and state; side effects belong strictly in event handlers or lifecycle effects.
- **State Management**: Prefer local component state and composition over monolithic global stores; always treat state as immutable.
- **Subsystem Specialization**: Specific hooks, version-specific APIs, and form handling primitives are documented in dedicated child knowledges.
</knowledge:example>

### Frontmatter Rules
- `title`: Required concise, unambiguous topic title.
- `version`: Optional version string of the documented technology or API (defaults to "1.0.0").
- `source`: Recommended list of authoritative, canonical URLs. Ingest only canonical documentation; omit blogs, forums, or aggregators.
- `version_check`: Optional package or command version detector: `npm:<pkg>`, `pypi:<pkg>`, `github:<owner>/<repo>`, `crates:<crate>`, `scrape:<url>#<regex>`, or `terminal:<cmd>`; i.e. `npm:react`, `pypi:numpy`. Should be machine readable.
- `tags`: Optional list of 3–8 concise, lowercase keyword tags formatted as: i.e. `["library", "javascript", "ui"]`. Maximum 10 tags. Avoid redundant synonyms, generic words, or sentences.
- `related`: Optional list of related knowledge document paths. MUST ONLY reference existing knowledges or active drafts; do not invent non-existent paths. May be empty.

## Sizing and Formatting Discipline
- **Token Bounds**: Leaf nodes MUST target 500 to 1,000 tokens (LowerTarget = 500, UpperTarget = 1,000). Hard maximum is 1,800 tokens. Oversized nodes (>1,800 tokens) will trigger refactoring escalation.
- **High Signal-to-Noise**: Avoid conversational filler ("In this document we will explore..."). Begin immediately with actionable rules and architectural primitives.
- **Markdown formatting**: Should be well-formatted GitHub-Flavored markdown.
  * Utilize markdown formatting(bold, italic, ...) to highlight, but do not overuse
</knowledge:spec>
