# LibHippo: Permanent Knowledge Library for LLM
## Architecture Proposal & System Specification

> **Project Name**: LibHippo (`libhippo`)  
> **Base Framework**: AutoGen 0.4 (`autogen-agentchat`, `autogen-core`)  
> **Core Architecture**: Cascading Hub-Leaf Knowledge Store, 3-Tier Adaptive Retrieval, Maker-Checker Governance Loop

---

## 1. Problem Definition

### 1.1 Background & Limitations of Existing Agent Architectures
1. **Context Window Bottlenecks and Repeated Retrieval Overhead**:
   - Accumulating raw conversational context over long-horizon tasks leads to prohibitive token costs and the "Lost in the Middle" phenomenon, causing hallucinations or forgetting.
   - Once a session terminates, knowledge evaporates, forcing agents to re-parse massive external documentation or files from scratch in subsequent sessions.
2. **Inefficiencies of Rigid Path Traversal**:
   - When knowledge is stored in strict hierarchical directories (`category/topic/item.md`), requiring agents to guess and provide exact file paths creates repetitive exploratory tool loops and latency spikes.
3. **Knowledge Staleness & Uncontrolled Documentation Decay ("Vibe-Coding" Fallout)**:
   - LLM pre-training data is fixed at a point in time, trailing behind bleeding-edge specifications (e.g., modern framework updates, evolving standards).
   - If agents are permitted to create markdown notes without strict editorial oversight, unvetted, redundant, and conflicting documents rapidly pollute the knowledge base.

### 1.2 Target Goals
- **High-Velocity Semantic Retrieval**: Instant lookup of pre-structured knowledge at the appropriate coarseness level, keeping the active context lean.
- **3-Tier Adaptive Knowledge Dispatch**: Deterministic low-latency retrieval for straightforward lookups alongside autonomous agent-driven triage for ambiguous or complex queries.
- **Living Knowledge Governance**: A closed-loop workflow that continuously validates, deprecates, and compacts knowledge via an isolated Maker-Checker mechanism.

---

## 2. System Architecture & Agent Topologies

The system comprises 4 specialized, complementary agents coordinated via an AutoGen `GraphFlow`.
Knowledge retrieval is governed by the **`query_knowledge` 3-tier adaptive dispatcher**, and knowledge updates are governed by an isolated **Maker-Checker pattern (`CuratorAgent` drafts, `VerifyAgent` audits and commits)**.

```mermaid
graph TD
    User([User Prompt / Task Request]) --> TaskSolver[TaskSolverAgent<br>Problem Solving & Code Generation<br><b>gpt-4o</b>]
    
    subgraph Adaptive Retrieval: query_knowledge
        TaskSolver -->|1. query_knowledge call<br>Parallel Tool Calling Supported| Router{Effort Router}
        Router -->|low_effort<br>Fast Vector/FTS<br>Fails if conf < th_low| LowCheck{Confidence >= th_low?}
        LowCheck -->|Yes: conf met| FastPath[(Local Vector Store & Hub-Leaf<br>Raw Snippet Extraction)]
        LowCheck -->|No: conf not met| LowFail([Return MISS / Failure<br>No LLM Invocation])
        
        Router -->|medium_effort<br>Optimistic Fast-Path| MedCheck{Confidence >= th_med?}
        MedCheck -->|Yes: conf met| FastPath
        MedCheck -->|No: conf not met| BookKeeper[BookKeeperAgent<br>Librarian Subagent<br><b>gpt-4o-mini</b>]
        
        Router -->|high_effort<br>Unconditional Deep Path| BookKeeper
        
        BookKeeper <-->|Tool: Query decomposition & cross-search| FastPath
        FastPath -->|Direct raw snippets (<50ms)| TaskSolver
        BookKeeper -->|Synthesized snippets or MISS tag| TaskSolver
        LowFail -->|MISS notification| TaskSolver
    end

    subgraph Knowledge Curation: On MISS:MANDATORY
        TaskSolver -.->|2a. Missing mandatory knowledge<br>Request targeted scrape & draft| Curator[CuratorAgent<br>Draftsman: Web Scrape + Knowledge Synthesis<br><b>gpt-4o-mini</b>]
        Curator <-->|Tool: Web Fetch| Web([Official Documentation / Web])
        Curator -->|2b. Propose markdown diff draft| VerifyAgent[VerifyAgent<br>Auditor & Gatekeeper<br><b>gpt-4o</b>]
        VerifyAgent <-->|Tool: mutate_knowledge_and_vector<br>Atomic disk commit upon approval| FastPath
        VerifyAgent -->|2c. Approved knowledge injection| TaskSolver
    end

    TaskSolver -->|3. Submit solution & code draft| VerifyAgent

    subgraph Evaluation & Feedback Loop
        VerifyAgent -->|4a. Reject: Outdated API / Standard violation| TaskSolver
        VerifyAgent -->|4b. Discover new rules or order deprecation| Curator
    end

    VerifyAgent -->|5. Final Approval: APPROVE TERMINATE| Output([Final Solution & Knowledge Report])
```

---

## 3. Agent Specifications, Models, and Context Isolation

To maximize cost efficiency and operational throughput, models and reasoning effort / temperatures are differentiated by role complexity:

| Agent Name | Recommended Model | Reasoning Effort / Temp | Context Isolation Strategy | Core Responsibilities |
| :--- | :--- | :--- | :--- | :--- |
| **`TaskSolverAgent`** | `gpt-4o` | **Medium**<br>(Temp 0.2~0.4) | Main conversational thread (Prompt-cache friendly linear context) | Business logic analysis, code generation, parallel `query_knowledge` execution |
| **`BookKeeperAgent`** | `gpt-4o-mini` | **Minimal**<br>(Temp 0.0) | **Zero-Context Sandbox**<br>(Spun up only on `medium` fallback or `high` effort) | Query decomposition, synonym expansion, multi-leaf cross-referencing, HIT/MISS determination |
| **`CuratorAgent`** | `gpt-4o-mini`<br>(or `gpt-4o` for complex frameworks) | **Low**<br>(Temp 0.1) | Conditional sub-workflow (Active only on `MISS:MANDATORY` or deprecation orders) | Targeted web scraping, synthesis of external facts with LLM reasoning, markdown diff drafting |
| **`VerifyAgent`** | `gpt-4o`<br>(or `o3-mini`) | **High / Strict**<br>(Temp 0.0, max effort) | Independent evaluation session | Code specification audit, **sole authority to commit to disk (`modify_knowledge`)**, termination control |

---

### 3.1 `TaskSolverAgent` (Task Executor)
- **Model**: `gpt-4o` (Optimized for coding precision and strict adherence to technical constraints).
- **Hyperparameters**: `temperature: 0.2 ~ 0.4`.
- **Role & Interface**:
  - Analyzes user requirements and issues `query_knowledge(query, effort, criticality)` calls.
  - Supports **Parallel Tool Calling**: When tackling multifaceted tasks, it dispatches multiple discrete queries simultaneously.
  - Generates self-contained, disambiguated queries:
    - Low Effort: `query_knowledge(query="HTML Button syntax", effort="low")`
    - Medium Effort (Default): `query_knowledge(query="HTML custom button ARIA role keyboard accessibility", effort="medium")`
    - High Effort: `query_knowledge(query="HTML button blinks when hovered CSS transform translate-x suspected", effort="high")`
  - Consumes raw snippets returned directly by the fast path or synthesized summaries from `BookKeeperAgent`.
- **Input**: User prompt, retrieved knowledge snippets, `VerifyAgent` feedback.
- **Output**: Implementation code draft, technical summary.

### 3.2 `BookKeeperAgent` (Adaptive Librarian Subagent)
- **Model**: `gpt-4o-mini` (High speed, structured output adherence, 1/15th cost of flagship models).
- **Hyperparameters**: `temperature: 0.0`, Reasoning Effort: None/Minimal.
- **Operational Logic**:
  - **Zero-Context Sandbox**: Operates without conversational history, relying solely on the disambiguated query and librarian system prompt.
  - **Zero Re-summarization**: Because documents are pre-partitioned into Hub (overview) and Leaf (details), the agent extracts and forwards **raw text snippets verbatim**, preventing information degradation and saving tokens.
  - **Query Expansion & Disambiguation**: Decomposes natural language symptoms into technical indices, checks synonyms, evaluates cross-references, and outputs status tags (`[HIT]`, `[MISS:MANDATORY]`, `[MISS:FALLBACK]`).
- **Input**: `query_knowledge(query, effort="medium"|"high", criticality)`.
- **Output**: Target markdown file path, verbatim snippet blocks, hit/miss status tags.

### 3.3 `CuratorAgent` (Knowledge Draftsman)
- **Model**: `gpt-4o-mini` (or `gpt-4o` for intricate cross-stack migrations).
- **Hyperparameters**: `temperature: 0.1`, Reasoning Effort: Low.
- **Role & Permissions**:
  - Triggered exclusively on `MANDATORY` knowledge misses or deprecation instructions from `VerifyAgent`.
  - Employs `search_web` and `fetch_web` to retrieve official documentation and specifications.
  - Blends verified external facts with general programming conventions into standard Hub-Leaf markdown diffs.
  - **Write-Protected**: Lacks disk write capabilities (`modify_knowledge`), submitting drafts solely to `VerifyAgent`.
- **Input**: Missing topic descriptor, target URLs, verifier change requests.
- **Output**: Proposed markdown diff (addition, amendment, or deprecation).

### 3.4 `VerifyAgent` (Quality Auditor & Gatekeeper)
- **Model**: `gpt-4o` (or `o3-mini` with strict reasoning effort).
- **Hyperparameters**: `temperature: 0.0`, Reasoning Effort: High / Strict.
- **Exclusive Commit Authority & Gatekeeping**:
  - **Solution Audit**: Scrutinizes code from `TaskSolverAgent` for deprecated APIs, security antipatterns, and style violations.
  - **Exclusive Disk Commit**: Validates markdown diffs from `CuratorAgent` for redundancy and consistency, and is the **only agent permitted to execute `modify_knowledge`** to atomically update local disk files and vector collections.
  - **Session Termination**: Emits `[APPROVE: TERMINATE]` once all quality gates pass.
- **Input**: Code draft from TaskSolver, markdown diff proposal from Curator.
- **Output**: Approval termination tag, actionable revision feedback (`REVISE`), or disk mutation executions.

---

## 4. Cascading Knowledge Scopes & Storage Design

The knowledge repository is organized into hierarchical, scoped namespaces where granular rules override broader defaults:
$$\text{Project (Highest)} > \text{User (Preferences)} > \text{Plugins (Optional Packs)} > \text{Common (General Standards)}$$

### 4.1 Hub-and-Leaf Directory Structure
Every directory pairs with a sibling markdown file of identical basename. The parent file functions as a **Hub Document (coarse overview and child index)**, while internal files act as **Leaf Documents (fine-grained rules, edge cases, and code patterns)**.

```text
libhippo/knowledge/
├── knowledge_catalog.db              <-- Local SQLite FTS5 / metadata catalog index
├── common.md                         <-- Level 0 (Root Hub): General domain standard overview
├── common/
│   ├── web.md                        <-- Level 1 (Domain Hub): Web technology standards
│   └── web/
│       ├── html.md                   <-- Level 2 (Category Hub): HTML5 rules & semantic structure
│       └── html/
│           ├── syntax.md             <-- Level 3 (Leaf): Concrete syntax & tags
│           └── accessibility.md      <-- Level 3 (Leaf): ARIA & keyboard navigation edge cases
├── user.md                           <-- User Root Hub
├── user/
│   └── preferences.md                <-- User coding styles & preferred packages
├── project.md                        <-- Project Root Hub (Tracked in Git)
├── project/
│   └── architecture.md               <-- Repository architecture & schema rules
├── plugins/                          <-- Modular plug-and-play knowledge bundles
│   └── react19.md / react19/
└── deprecated/                       <-- Quarantined, superseded knowledge records
```

### 4.2 Local Vector Store Indexing & Atomic Synchronization
- **Source of Truth**: Local human-readable, Git-tracked **Markdown files (`.md`)**.
- **Index Accelerator**: Local **Vector Store (ChromaDB or SQLite-vec)** storing chunk embeddings and metadata for fast similarity lookup.
- **Instant Synchronization**: When `VerifyAgent` writes a modification or archives a document into `deprecated/`, active entries are immediately re-indexed, preserving 100% search freshness without fine-tuning model weights.

### 4.3 Markdown Node Schema Example (`common/web/html/accessibility/aria_button.md`)
```markdown
---
title: "Button Accessibility with ARIA"
namespace: "common" # common | user | project | plugins
level: "leaf"
coarseness: 3
version: "WAI-ARIA 1.2"
status: "active" # active | deprecated | needs_review
last_updated: "2026-09-28"
related:
  - "common/web/html/syntax/semantic_tags.md"
tags: ["html", "a11y", "aria", "button"]
access_count: 0
last_accessed: "2026-09-28"
nature: "critical_rule" # foundation | critical_rule | transient_tip
---

## Summary (Coarse View)
Use native `<button>` whenever possible. Only use `role="button"` on `<div>` with `tabindex="0"` and keyboard listeners (Enter/Space).

## Detailed Rules & Edge Cases (Fine View)
- Ensure preventDefault() on Space key to avoid page scroll.
- Add aria-pressed for toggle buttons.
```

### 4.4 Prompt-Cache Friendly Linear Context & Lazy Compaction
To leverage modern LLM prefix caching (e.g., OpenAI Prompt Caching) without incurring cache invalidation from sliding windows or head-summary-tail rewrites:

```text
[Prompt-Cache Maximized 3-Zone Architecture]
┌────────────────────────────────────────────────────────────────────────┐
│ Zone 1: Immutable Prefix (Guaranteed 100% Cache Read)                  │
│  - System Instructions + User/Project Profile summaries + Catalog spec │
│  👉 Unchanged across the entire session -> 50~80% cost & latency drops │
├────────────────────────────────────────────────────────────────────────┤
│ Zone 2: Append-Only Linear History (KV-Cache Extension Zone)           │
│  - User prompt + Agent reasoning trace (CoT)                           │
│  - query_knowledge tool calls & returned raw markdown snippets         │
│  👉 Sequential addition preserves previous turn KV-cache entirely      │
├────────────────────────────────────────────────────────────────────────┤
│ Zone 3: Lazy Compaction (Triggered only when exceeding token quota)    │
│  - Trigger: Cumulative tokens exceed threshold (e.g., 8,000 tokens)    │
│  - Action: Retains reasoning traces, but evicts bulky past tool outputs│
│    (replaces raw snippets with '[Referenced: common/.../syntax.md]')   │
└────────────────────────────────────────────────────────────────────────┘
```

### 4.5 3-Tier Query Criticality
To avoid unnecessary web lookups when information is missing:
1. **`MANDATORY`**: Strict security rules or framework version constraints $\rightarrow$ On miss, triggers `CuratorAgent` to fetch official external docs.
2. **`PREFERRED`** (Default): Common idioms and conventions $\rightarrow$ On miss, falls back to `TaskSolverAgent`'s internal knowledge (0 web search overhead).
3. **`OPTIONAL`**: Auxiliary helpers or nice-to-haves $\rightarrow$ On miss, skipped immediately.

---

## 5. Core Tool Specifications

### 5.1 `query_knowledge` (Adaptive Knowledge Retrieval Tool)

```python
async def query_knowledge(
    query: str,
    effort: Literal["low", "medium", "high"] = "medium",
    criticality: Literal["mandatory", "preferred", "optional"] = "preferred",
    threshold_low: float = 0.70,
    threshold_medium: float = 0.82,
) -> KnowledgeRetrievalResult:
    """
    Queries the LibHippo knowledge base using a 3-tier effort strategy:
    
    1. effort="low":
       - Executes local Vector / FTS search (<50ms, 0 LLM cost).
       - Evaluates if top match confidence >= threshold_low.
       - If met: Returns matching raw document snippets directly.
       - If NOT met: Fails immediately (returns [MISS:FALLBACK], no LLM fallback).
       
    2. effort="medium" (Default):
       - Executes local Vector / FTS search first.
       - Evaluates if top match confidence >= threshold_medium.
       - If met: Returns raw snippets directly (<50ms, 0 LLM cost).
       - If NOT met: Automatically escalates to BookKeeperAgent (gpt-4o-mini).
         The librarian performs query expansion, multi-leaf cross-search,
         and marks the query as [HIT], [MISS:MANDATORY], or [MISS:FALLBACK].
         
    3. effort="high":
       - Bypasses single-vector matching.
       - Directly invokes BookKeeperAgent for multi-hop synthesis,
         cross-domain bug investigations, and query decomposition.
    """
```

### 5.2 Tool Registry Overview

| Tool Name | Caller | Input Arguments | Functional Description |
| :--- | :--- | :--- | :--- |
| **`query_knowledge`** | `TaskSolverAgent` | `query: str`, `effort: "low"\|"medium"\|"high"`, `criticality: "mandatory"\|"preferred"\|"optional"`, `threshold_low: float`, `threshold_medium: float` | Dispatches query across the 3 effort tiers, returning raw snippets or librarian synthesis. |
| **`search_knowledge`** | `BookKeeperAgent` | `query: str`, `top_k: int = 3`, `namespace: str = None` | Searches the local Vector Store (ChromaDB/SQLite-vec) by cosine similarity, returning node metadata and file paths. |
| **`read_knowledge`** | `BookKeeperAgent` | `file_path: str`, `section: "summary"\|"rules"\|"full"` | Reads the specified section from a Hub or Leaf markdown document without lossy re-summarization. |
| **`fetch_web`**, **`search_web`** | `CuratorAgent` | `search_query: str`, `doc_url: str = None` | Conducts targeted searches against official documentation domains and scrapes technical specifications. |
| **`modify_knowledge`** | `VerifyAgent`<br>*(Sole Authority)* | `action: "create"\|"update"\|"purge"`, `path: str`, `content: str`, `metadata: dict` | Atomically commits markdown changes to the local filesystem and updates the vector index. |

---

## 6. Termination Conditions

1. **Normal Termination**:
   - `VerifyAgent` completes its inspection of the solution code and any associated knowledge updates.
   - Emits `[APPROVE: TERMINATE]` in its final response, fulfilling the `TextMentionTermination` criterion.
2. **Safety Guardrail Termination**:
   - `MaxMessageTermination(max_messages=16)`: Prevents infinite discussion loops by terminating execution after 16 conversational rounds, outputting current artifacts and unresolved blockers.
   - Per-path tool invocation retry limit: Maximum 2 consecutive retry attempts on identical knowledge paths.

---

## 7. Failure Recovery Workflows

| Failure Mode | Root Cause | System Recovery Workflow |
| :--- | :--- | :--- |
| **1. Stale Knowledge Collision** | The repository contains an outdated specification, while the user expects modern APIs (e.g., React 19). | `VerifyAgent` flags obsolete syntax $\rightarrow$ `CuratorAgent` marks old document as `deprecated/` and drafts modern replacement $\rightarrow$ `VerifyAgent` commits to disk $\rightarrow$ `TaskSolverAgent` regenerates code. |
| **2. Low-Effort Retrieval Miss** | `effort="low"` query failed because match confidence was below `threshold_low`. | Fast failure returned to `TaskSolverAgent` $\rightarrow$ Solver can either proceed using baseline LLM common sense or escalate to `effort="medium"`/`"high"`. |
| **3. Catalog Miss on Mandatory Topic** | `BookKeeperAgent` cannot find documentation required by a `MANDATORY` constraint. | BookKeeper emits `[MISS:MANDATORY]` $\rightarrow$ `CuratorAgent` executes targeted web scrape $\rightarrow$ `VerifyAgent` reviews and commits new leaf $\rightarrow$ Fresh snippet injected into Solver context. |
| **4. Documentation Bloat ("Vibe-Coding" Clutter)** | Repetitive markdown notes created for minor edge cases. | `VerifyAgent` rejects new file creation, directing `CuratorAgent` to append a compact 1-line rule into an existing Leaf document's `Detailed Rules` section. |
