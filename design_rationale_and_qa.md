# LibHippo Architecture Design Rationale & Q&A Log

> This document captures the **core engineering trade-offs, design dilemmas, architectural rationales, model evaluation benchmarks, and implementation decisions** discussed during the design of **LibHippo** (`libhippo`). For concrete system schemas and class contracts, refer to [architecture_proposal.md](file:///mnt/d/develop/ai/libhippo/architecture_proposal.md).

---

## 1. Knowledge Base and RAG Architecture Trade-Offs

### Q1. Isn't summarizing retrieved documents in a separate session already standard practice in RAG?
- **Analysis**:
  - The conventional pattern—"retrieve chunks, run an LLM summarizer, inject the summary into context"—suffers from two major inefficiencies:
    1. **Redundant Runtime Latency & Token Burn**: The LLM reads and summarizes the same raw documents over and over again across different queries.
    2. **Ephemeral Value**: Dynamically generated summaries evaporate once the conversational session terminates. Future queries on similar topics must rerun the entire RAG pipeline from scratch.
- **LibHippo's Differentiator**:
  - Instead of summarizing on-the-fly at query time, the repository itself is pre-structured into a persistent **Hub (coarse summary & navigation index)** and **Leaf (granular implementation rules and edge cases)** markdown hierarchy.
  - The system directly extracts raw snippets from the appropriate coarseness tier without any runtime LLM summarization pass: **runtime summarization cost is exactly $0**.

### Q2. Why keep Markdown (`.md`) files instead of using a pure Vector Store?
- **Analysis**:
  - Storing embeddings exclusively in a vector database creates an opaque black box. Developers cannot easily inspect, edit, or audit why an agent holds a particular belief.
  - Pure vector stores cannot leverage Git for version control, code reviews, branch merging, or rollbacks.
- **LibHippo's Hybrid Architecture**:
  - **Source of Truth**: Local, human-readable, Git-tracked **Markdown documents**.
  - **Index Accelerator**: A local **Vector Store (ChromaDB or SQLite-vec)** that indexes markdown metadata, tags, and section chunks for sub-millisecond semantic search.

### Q3. Why not fine-tune a small local language model (SLM) to act as the BookKeeper?
- **Analysis**:
  - Fine-tuning and self-hosting an SLM introduces high operational overhead (GPU dependencies, local server provisioning, deployment fragility across diverse developer setups).
  - Every knowledge update would require either complex Continual Learning pipelines or LoRA retraining, risking catastrophic forgetting or stale weights.
- **Selected Pragmatic Alternative**:
  - When markdown files change or get archived into `deprecated/`, LibHippo re-indexes the local vector store in $<1$ second. This maintains a 100% fresh, hallucination-resistant index without modifying model weights.

---

## 2. Knowledge Criticality and Curation Cost Control

### Q1. How do we prevent runaway web scraping and validation costs when knowledge is optional?
- **The Dilemma**:
  - Triggering web search on every knowledge miss causes explosive latency and API cost spikes.
  - However, asking an LLM to rate importance as a continuous float ($0.0 \sim 1.0$) leads to numerical inconsistency and unpredictable routing.
- **Resolution: 3 Discrete Criticality Tiers**:
  - `MANDATORY`: Strict organizational security standards or library API requirements $\rightarrow$ Web search and curation are obligatory upon retrieval miss (`CuratorAgent` is launched).
  - `PREFERRED` (Default): Common idioms, component templates, or style guidelines $\rightarrow$ On miss, web search is avoided; the system falls back directly to `TaskSolverAgent`'s internal programming knowledge (**0 web search overhead**).
  - `OPTIONAL`: Auxiliary utility functions or convenience helpers $\rightarrow$ On miss, skipped immediately (**0 overhead**).

### Q2. Hybrid Synthesis: Merging Official Web Facts with LLM Reasoning
- **The Dilemma**: Relying purely on internal LLM weights leads to hallucinations and deprecated API usage (e.g., outdated React APIs). Conversely, raw web scraping often produces fragmented or ill-structured text snippets unsuitable for coding context.
- **Resolution**:
  - `CuratorAgent` performs targeted scrapes against authoritative documentation for precise technical parameters and contracts.
  - It synthesizes these extracted facts with standard idiomatic programming practices, formatting the output into structured Hub-and-Leaf markdown diff proposals.

### Q3. Why restrict disk write permissions exclusively to `VerifierAgent`? (4-Eyes Principle)
- **The Dilemma**: Allowing `CuratorAgent` to write directly to disk leads to rapid documentation decay—duplicate notes, poorly formatted markdown files, and inconsistent metadata generated during unstructured "vibe-coding" sessions.
- **Resolution: Maker-Checker Governance**:
  - `CuratorAgent` is strictly a **draftsman**: it can generate diff proposals but has no file write tools.
  - `CheckerAgent` and `VerifierAgent` act as the **dual-tier gatekeepers**: `CheckerAgent` validates structural properties and routine diffs; `VerifierAgent` is the sole authority to execute disk commits (`modify_knowledge`) and manage complex refactoring.

### Q4. Why decouple CheckerAgent (TypeSafe Jev) from VerifierAgent (LLM) for normal reviews?
- **The Trade-off**:
  - Running a full flagship LLM (`gpt-4o` or `o3-mini`) to review every routine 2-line markdown addition incurs severe token latency ($1\sim 3\text{s}$) and financial overhead. Furthermore, general LLMs are prone to occasional subtle schema drift in frontmatter formatting.
- **The Dual-Tier Resolution**:
  - **Routine Review Gate (`CheckerAgent` powered by TypeSafe Jev)**: For normal edits, additions, and updates, `CheckerAgent` acts as the primary gatekeeper. It executes deterministic, high-throughput structural evaluation:
    1. Schema conformance (valid YAML frontmatter types).
    2. Hierarchy and naming fit (`common/web/...`).
    3. Document sizing and hysteresis verification.
    4. Importance scoring ($0.0 \sim 1.0$).
    5. Sibling coalescence and Single Responsibility Principle (SRP) coherence.
  - If CheckerAgent emits `PASS`, the edit is approved and committed directly: **0 LLM token burn, sub-100ms latency**.
  - **LLM Escalation Gate (`VerifierAgent`)**: The heavy LLM is reserved exclusively for:
    1. Code draft audits against project conventions.
    2. Architectural refactoring when CheckerAgent flags an `OVERSIZED` document (designing the directory split and partition plan).
    3. Semantic deprecation conflicts.

### Q5. How does LibHippo handle knowledge hierarchy growth and fragmentation with Hysteresis (Split vs. Merge)?
- **The Problem & Thrashing Risk**:
  - Over time, popular files accumulate edge cases and grow into bloated monoliths that degrade vector chunking relevancy. Conversely, over-eager agents generate dozens of micro-files with a single bullet point each, resulting in directory sprawl.
  - Furthermore, if split and merge thresholds are too close, documents hovering around that boundary will oscillate between splitting and merging upon minor edits (**thrashing**), causing heavy filesystem I/O, vector re-embedding, and link churn.
- **The Hysteresis Resolution**:
  - **Split Upper Bound ($\theta_{\text{split}} \ge 1,800$ tokens)**: Elevates `web.md` to a Hub document, creates directory `web/`, and partitions subtopics into child leaves (`web/a.md`, `web/b.md`, ...).
  - **Merge Lower Bound ($\theta_{\text{merge}} \le 300$ tokens)**: Rejects isolated creation of micro-files (e.g. proposed `input.md` alongside `button.md, label.md, checkbox.md`) and coalesces them into composite leaves (`form_controls.md`) or parent Hub rules.
  - **Stability Deadband ($[300, 1800]$ tokens)**: Documents in this neutral range are strictly immune to automatic split or merge actions. The wide 1,500-token gap guarantees stability against rapid oscillation without needing artificial cooldown timers.
  - **Composite Sizing (Code Arithmetic + Jev Semantic Judgment)**:
    - Pure token counting is an exact arithmetic operation handled deterministically by local code (<1ms via `tiktoken`). Jev is never asked to count tokens.
    - Instead, Jev measures **semantic bloatiness** (sparse stub vs. lean density vs. discursive verbosity vs. monolithic over-packing).
    - Code combines raw token count with Jev's bloatiness score into an **Effective Token Size**. This allows conceptually bloated, rambling documents to trigger an architectural split even at 1,400 raw tokens, while dense, compact reference tables at 1,700 tokens remain protected within the deadband.

### Q6. Why score document importance, and how does it influence retrieval without overpowering relevance?
- **The Problem**: Pure semantic cosine similarity can sometimes rank obscure, highly specific edge cases or transient tips higher than fundamental architectural standards simply because a user query happened to share idiosyncratic keywords.
- **The Solution (Subtle Importance Boost)**:
  - `CheckerAgent` computes `importance_score` ($0.0 \sim 1.0$) based on architectural permanence and criticality (foundational standard = 0.9, idiomatic rule = 0.6, transient tip = 0.2).
  - `query_knowledge` applies an importance-weighted confidence blend:
    $$\text{Confidence} = (1 - \alpha) \cdot \text{Sim}_{\text{cosine}} + \alpha \cdot \text{Score}_{\text{importance}} \quad (\alpha = 0.08)$$
  - Because $\alpha$ is small ($0.08$), semantic relevance remains overwhelmingly dominant—an irrelevant doc will never be retrieved just because it has high importance. But between candidate documents of comparable relevance, foundational standards reliably win tie-breakers and clear retrieval thresholds ($\tau_{\text{low}}=0.70, \tau_{\text{med}}=0.82$).

---

## 3. The BookKeeper Dilemma & 3-Tier Adaptive Retrieval

### Q1. Why can't `query_knowledge` just be a simple tool? Why involve an agent?
- **The Dilemma**:
  - In coding tasks, an agent often queries 3 to 4 related APIs concurrently via parallel tool calling. Launching a heavy LLM agent for every single lookup introduces severe latency ($1\sim 3\text{s}$) and token waste.
  - However, pure deterministic string/vector matching fails when:
    - Queries are ambiguous or symptom-based (e.g., *"why does my button blink on hover?"*).
    - Queries require multi-hop reasoning across multiple leaf documents.
    - Determining whether an index miss is truly absent or merely aliased requires semantic reasoning.

### Q2. The 3-Tier Effort Solution: Low vs. Medium vs. High
LibHippo resolves this tension by providing three explicit effort levels in `query_knowledge`:

```text
query_knowledge(query, effort="low" | "medium" | "high", criticality="mandatory" | "preferred" | "optional")
```

1. **`low_effort` (Strict Deterministic — Zero LLM Overhead)**:
   - Queries the local Vector Store and SQLite FTS5 index directly ($<50\text{ms}$, 0 token cost).
   - **Confidence Gating**: Evaluates whether the top match confidence meets $\ge \tau_{\text{low}}$ (e.g., $0.70$).
   - **Failure Policy**: If confidence is below $\tau_{\text{low}}$, it **fails immediately** (returns `[MISS:FALLBACK]` without invoking any LLM).
   - **Ideal for**: Rapid, unambiguous keyword/syntax lookups and high-volume parallel batch queries.

2. **`medium_effort` (Optimistic Fast-Path with Gated Escalation — Default)**:
   - Executes local vector/FTS search first.
   - **Confidence Gating**: Evaluates against a stricter confidence threshold $\tau_{\text{med}}$ (e.g., $0.82$).
   - If confidence is high ($\ge \tau_{\text{med}}$), it returns the raw snippets immediately ($<50\text{ms}$, 0 LLM cost).
   - If confidence falls below $\tau_{\text{med}}$ or top matches are ambiguous, it **automatically hands over to `BookKeeperAgent`** (`gpt-4o-mini`).
   - The agent performs query expansion, synonym aliasing, and multi-leaf cross-checks before issuing a formal `[HIT]` or `[MISS]` determination.
   - **Ideal for**: The vast majority of general programming tasks where a direct answer is likely available, but autonomous agent escalation is needed if direct matching falls short.

3. **`high_effort` (Unconditional Deep Agent Exploration)**:
   - Bypasses single-vector matching entirely.
   - Directly spins up `BookKeeperAgent` in its zero-context sandbox.
   - Performs query decomposition, sub-query routing, and multi-document correlation.
   - **Ideal for**: Complex bug triage with obscure symptoms, cross-domain interactions, and architectural investigations.

---

## 4. Context Isolation and Prompt Caching Optimization

### Q1. Is Zero-Context isolation outside `TaskSolverAgent` viable?
- **Analysis**:
  - Running subagents without the parent conversation history eliminates massive token transfer and keeps subagents focused.
  - **Risk**: Pronoun and co-reference ambiguity (e.g., *"apply a11y to that button"* fails if the subagent does not know what "that button" refers to).
- **Resolution**:
  - `TaskSolverAgent` is instructed to formulate **self-contained, disambiguated queries** before calling tools.
  - Example: `query_knowledge(query="HTML custom button ARIA role keyboard accessibility", effort="medium")`.
  - As a result, `BookKeeperAgent` operates flawlessly in a clean, stateless sandbox.

### Q2. Sliding Windows vs. Prompt-Cache Friendly Linear Context + Lazy Compaction
- **Flaw of Sliding Windows / "Head-Summary-Tail"**:
  - Modern LLM inference engines (e.g., OpenAI Prompt Caching) rely on **static prefix matching** to achieve 50–80% cost discounts and near-instant time-to-first-token (TTFT).
  - Summarizing or shifting conversational context in the middle of a session breaks the cache prefix on every single turn, dramatically increasing costs and latency.
- **LibHippo's 3-Zone Architecture**:
  1. **Zone 1: Immutable Prefix**: System prompt, static user preferences, project conventions, and knowledge catalog schema. (Never changes during the session $\rightarrow$ 100% cache hit rate).
  2. **Zone 2: Append-Only Linear History**: User turns, reasoning traces, and retrieved raw snippets appended sequentially. (Reuses prior turn KV-cache).
  3. **Zone 3: Lazy Compaction**:
     - Triggered only when cumulative token count crosses a high-water mark (e.g., 8,000 tokens).
     - Keeps the reasoning trace intact, but evicts bulky raw markdown tool outputs from older turns, replacing them with concise reference markers (`[Referenced: common/.../syntax.md]`).

---

## 5. Model Mix & Agent Capability Benchmarks

### 5.1 Model Assignment Strategy
| Agent | Model | Temperature / Effort | Allocation Rationale |
| :--- | :--- | :--- | :--- |
| **`TaskSolverAgent`** | `gpt-4o` | Temp 0.2~0.4 / Medium | Complex logic generation, high instruction-following fidelity. |
| **`BookKeeperAgent`** | `gpt-4o-mini` | Temp 0.0 / Minimal | High-speed indexing, schema validation, 1/15th cost. |
| **`CuratorAgent`** | `gpt-4o-mini` (or `gpt-4o`) | Temp 0.1 / Low | Synthesizing scraped technical facts with markdown templates. |
| **`VerifyAgent`** | `gpt-4o` (or `o3-mini`) | Temp 0.0 / High | Uncompromising code auditing, security checks, and gatekeeping. |

---

### 5.2 Real-World Agent Verification Benchmarks

#### Benchmark 1: BookKeeper Evaluation (Query Disambiguation & Index Decomposition)
```text
Context: The local catalog indexes HTML syntax, ARIA accessibility, and modern React 19 hooks.
User Symptom: "I built a button using a div, but screen readers and keyboards are misbehaving."

Task: Output valid JSON containing:
(1) Three expanded search keywords,
(2) Expected target file path (e.g., common/web/html/accessibility/aria_button.md),
(3) Assessed criticality tier (mandatory | preferred | optional).
```
- **Evaluation Criteria**: Returns exact keywords (`role="button"`, `tabindex="0"`, `keyboard events`), correct path, and valid JSON without extraneous commentary.

#### Benchmark 2: Curator Evaluation (Factual Web Synthesis into Markdown Diff)
```text
Fact from Official Documentation:
"In React 19, useActionState replaces useFormState. Signature: [state, formAction, isPending] = useActionState(fn, initialState)"

Task: Combine this fact with idiomatic React knowledge to draft a markdown node 
(common/web/react/actions.md) containing a 2-line [Summary] and a 3-bullet [Detailed Rules] section.
```
- **Evaluation Criteria**: Correctly documents the breaking change and return signatures, structuring the output strictly according to the Hub-Leaf markdown template.

#### Benchmark 3: VerifyAgent Evaluation (Strict Deprecation Detection)
```text
Context: You are a strict code quality auditor. Project convention requires React 19 standard compliance.
Inspect the following code draft submitted by TaskSolverAgent:

```javascript
const [state, formAction] = useFormState(action, null);
```

Task: Decide whether to APPROVE or REVISE. Provide actionable technical justification.
```
- **Evaluation Criteria**: Immediately flags `useFormState` as deprecated in React 19, instructs replacement with `useActionState`, and outputs `REVISE` with clear technical rationale.
