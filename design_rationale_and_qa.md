# LibHippo Architecture Design Rationale & Q&A Log

> This document captures the **core engineering trade-offs, design dilemmas, architectural rationales, and evaluation benchmarks** discussed during the design of **LibHippo** (`libhippo`). For concrete system schemas and class contracts, refer to [architecture.md](architecture.md).
> Previously discussed ideas may be present here, but they should be marked as obsolete.

---

## 1. Knowledge Base and RAG Architecture Trade-Offs

### Q1. Isn't summarizing retrieved documents in a separate session already standard practice in RAG?
- **Analysis**:
  - The conventional pattern—"retrieve chunks, run an LLM summarizer, inject summary into context"—suffers from two major inefficiencies:
    1. **Redundant Runtime Latency & Token Burn**: The LLM reads and summarizes the same raw documents repeatedly across queries.
    2. **Ephemeral Value**: Dynamically generated summaries evaporate when the session terminates. Subsequent queries re-run the entire pipeline from scratch.
- **LibHippo's Differentiator**:
  - The repository itself is pre-structured into a persistent **Hub (coarse summary & navigation index)** and **Leaf (granular implementation rules and edge cases)** markdown hierarchy.
  - The system directly extracts raw snippets from the appropriate coarseness tier without a runtime LLM summarization pass: **runtime summarization cost is exactly $0**.

### Q2. Why keep Markdown (`.md`) files instead of using a pure Vector Store?
- **Analysis**:
  - Pure vector stores create an opaque black box. Developers cannot easily inspect, audit, or edit why an agent holds a particular belief.
  - Vector databases cannot leverage Git for version control, code reviews, pull requests, or rollbacks.
- **LibHippo's Hybrid Architecture**:
  - **Source of Truth**: Local, human-readable, Git-tracked **Markdown documents**.
  - **Index Accelerator**: A local **Vector Store (ChromaDB or SQLite-vec)** that indexes markdown metadata, tags, and section chunks for sub-millisecond semantic search.

### Q3. Why not fine-tune a small local language model (SLM) to act as the BookKeeper?
- **Analysis**:
  - Fine-tuning and self-hosting an SLM introduces heavy operational overhead (GPU dependencies, local server provisioning, deployment fragility across developer setups).
  - Every knowledge update requires complex Continual Learning pipelines or LoRA retraining, risking catastrophic forgetting or stale weights.
- **Selected Pragmatic Alternative**:
  - When markdown files change or move to `deprecated/`, LibHippo re-indexes the local vector store in $<1$ second. This maintains a fresh, hallucination-resistant index without modifying model weights.

---

## 2. Knowledge Governance and Curation Cost Control

### Q1. How do we prevent runaway web scraping and validation costs when knowledge is optional?
- **The Dilemma**:
  - Triggering web searches on every knowledge miss causes explosive latency and API costs.
  - Asking an LLM to score importance as a continuous float ($0.0 \sim 1.0$) leads to numerical inconsistency and unpredictable routing.
- **Resolution: 3 Discrete Criticality Tiers**:
  - `MANDATORY`: Strict security standards or library API requirements $\rightarrow$ Web search and curation are obligatory upon retrieval miss (`CuratorAgent` is launched).
  - `PREFERRED` (Default): Common idioms and conventions $\rightarrow$ Web search is skipped on miss; the system falls back directly to `TaskSolverAgent`'s internal programming knowledge (**0 web search overhead**).
  - `OPTIONAL`: Auxiliary utility functions or convenience helpers $\rightarrow$ Skipped immediately on miss (**0 overhead**).

### Q2. Hybrid Synthesis: Merging Official Web Facts with LLM Reasoning
- **The Dilemma**: Relying purely on internal LLM weights leads to hallucinations and deprecated API usage. Conversely, raw web scraping produces fragmented, noisy snippets unsuitable for coding context.
- **Resolution**:
  - `CuratorAgent` performs targeted scrapes against authoritative documentation for precise technical parameters and contracts.
  - It synthesizes these extracted facts with idiomatic programming practices, formatting the output into structured Hub-and-Leaf markdown diff proposals.

### Q3. Why restrict disk write permissions exclusively to `VerifierAgent`? (4-Eyes Principle)
- **The Dilemma**: Allowing `CuratorAgent` to write directly to disk leads to rapid documentation decay—duplicate notes, poorly formatted markdown files, and inconsistent metadata generated during unstructured "vibe-coding" sessions.
- **Resolution: Maker-Checker Governance**:
  - `CuratorAgent` is strictly a **draftsman**: it can generate diff proposals but has no file write tools.
  - `CheckerAgent` and `VerifierAgent` act as dual-tier gatekeepers: `CheckerAgent` validates structural properties and routine diffs; `VerifierAgent` is the sole authority to execute disk commits (`modify_knowledge`) and manage complex refactoring.

### Q4. Why decouple CheckerAgent (TypeSafe Jev) from VerifierAgent (LLM) for normal reviews?
- **The Trade-off**:
  - Running a full flagship LLM (`gpt-6.1-sol` or `gpt-6-astra`) to review every routine 2-line markdown addition incurs severe token latency ($1\sim 3\text{s}$) and financial overhead, alongside subtle schema drift risks.
- **The Dual-Tier Resolution**:
  - **Routine Review Gate (`CheckerAgent` powered by TypeSafe Jev)**: Validates schema conformance, hierarchy fit, sizing bounds, importance/effectiveness scoring, content quality, and sibling coalescence. Approved diffs (`PASS`) commit directly: **0 LLM token burn, sub-100ms latency**.
  - **LLM Escalation Gate (`VerifierAgent` powered by `gpt-6.1-sol`)**: Heavy LLM reasoning is reserved strictly for high-order architectural decisions: hierarchy refactoring (`split/group`) on `OVERSIZED` nodes, and semantic deprecation conflicts. *(Task solution code auditing is decoupled to `architecture_runner.md`)*.

### Q5. Why use bidirectional hysteresis and asymmetric metrics for document length regulation?
- **The Thrashing Risk**:
  - Symmetrical thresholds cause thrashing (rapid splitting and merging upon minor edits; e.g., adding 5 tokens triggers a split, pruning 5 tokens triggers a merge).
  - LibHippo introduces a calibrated hysteresis gap between triggers and targets:
    $$\text{lower_bound_trigger} \,(300) < \text{lower_bound_target} \,(500) < \text{upper_bound_target} \,(1000) < \text{upper_bound_trigger} \,(1800)$$
- **Rationale for Asymmetric Sizing Metrics**:
  - **Upper Bound Split**: Uses composite $\text{EffectiveSize} = \text{TokenCount} \cdot (0.75 + 0.35 \cdot \text{Score}_{\text{bloatedness}})$. Semantic verbosity and topic stuffing indicate poor architectural separation and justify splitting even if raw tokens are moderate.
  - **Lower Bound Merge**: Uses deterministic raw $\text{TokenCount}$. High informational density must not be penalized; a concise, high-signal 250-token primitive should not be falsely merged simply because it is brief.
- **Dynamic Modulation & Post-Merge Edge Cases**:
  - Cohesive single-responsibility documents receive higher split headroom (up to 2,200 tokens) via coherence modulation.
  - Distinct primitives under diverse hubs receive lower merge thresholds (down to 150 tokens) to avoid unnatural combinations. If an undersized doc cannot be cleanly merged, it is either kept as an exception or pruned.
  - *(See [architecture.md#45-knowledge-length-regulation-hysteresis-token-count](architecture.md#45-knowledge-length-regulation-hysteresis-token-count) for complete formulas and contracts).*

### Q6. Why score document importance, and how does it influence retrieval without overpowering relevance?
- **The Problem**: Pure semantic cosine similarity can rank obscure, keyword-heavy edge cases higher than foundational architectural standards.
- **The Solution (Subtle Importance Boost)**:
  - `CheckerAgent` computes `importance_score` ($0.0 \sim 1.0$) based on architectural permanence and criticality.
  - `query_knowledge` applies an importance-weighted confidence blend:
    $$\text{Confidence} = (1 - \alpha) \cdot \text{Sim}_{\text{cosine}} + \alpha \cdot \text{Score}_{\text{importance}} \quad (\alpha = 0.08)$$
  - Because $\alpha = 0.08$, semantic relevance remains overwhelmingly dominant—irrelevant docs are never retrieved. However, between candidate documents of comparable relevance, foundational standards reliably win tie-breakers and clear retrieval confidence gates ($\tau_{\text{low}}=0.70, \tau_{\text{med}}=0.82$).

### Q7. Why separate Rule Effectiveness from Content Importance, and why use Damped Max-Blend?
- **The Dilemma**:
  - If importance only reflects content permanence and domain depth, cosmetic or local conventions (e.g., *"use single-quote instead of double-quote"*) receive a negligible score ($I_{\text{content}} \approx 0.10$). Consequently, they risk being pruned as low-importance stubs or losing retrieval tie-breakers, despite being non-negotiable project invariants that must always be enforced.
- **The Dual-Dimension Resolution**:
  - `CheckerAgent` decouples **Content Importance ($I_{\text{content}}$)** (architectural depth/permanence) from **Rule Effectiveness ($E_{\text{rule}}$)** (prescriptive authority/strictness).
  - Both dimensions are combined into `final_importance` via **Damped Max-Blend**:
    $$I_{\text{final}} = \max(I, E) + \lambda \cdot \min(I, E) \cdot (1 - \max(I, E)) \quad (\lambda = 0.20)$$
  - **Boundary Invariance**: If either $I = 1.0$ or $E = 1.0$, $I_{\text{final}} = 1.0$ exactly. A strict lint/convention rule receives $I_{\text{final}} = 1.0$.
  - **Controlled Moderate Synergy**: When both are moderate ($I=0.6, E=0.6$), $I_{\text{final}} \approx 0.65$—providing a gentle tie-breaker boost without the excessive inflation of raw Noisy-OR ($0.84$).

### Q8. How does CheckerAgent verify content quality without penalizing non-actionable reference knowledge?
- **The Dilemma**:
  - Checking content quality is critical to prevent messy drafts, grammatical errors, and conversational LLM fluff from polluting the knowledge base.
  - However, enforcing a naive "actionability" check would unfairly penalize declarative reference knowledge (e.g., syntax sheets, dictionaries, API tables) that contain no imperative commands.
- **The Resolution: Dual-Spectrum Practical Utility & REVISE_CONTENT**:
  - `practical_utility` evaluates **actionable directives** for prescriptive rules, and **precision/completeness** for declarative references. Syntax dictionaries score $1.0$ as readily as strict rules.
  - Deterministic Python guards catch syntax errors (unmatched code fences, invalid YAML frontmatter) at 0 token cost.
  - Content quality failures (grammar $< 0.40$, markdown formatting $< 0.40$, practical utility $< 0.30$, or unclosed code fences) emit `verdict = "REVISE_CONTENT"`, distinct from schema errors (`REVISE_SCHEMA`).

### Q9. How is context managed in iterative Check-Verify-Curate refactoring loops?
- **The Dilemma**:
  - Complex hierarchy refactoring (`OVERSIZED` partitions) may require multiple cycles of `Checker` $\rightarrow$ `Verifier` $\rightarrow$ `Curator` $\rightarrow$ `Checker`. Blindly dumping full documents and diffs across rounds causes rapid context saturation and cache invalidation.
- **Resolution (Linear Stacking + Compact Exceed Context + Cache Writing)**:
  - **Linear Stacking**: Turns append sequentially, preserving prefix cache continuity during active refactoring discussion.
  - **Prompt Cache Write: ENABLED**: Because multi-turn refactoring loops iteratively refine proposals across rounds, caching the linear prefix allows subsequent turns to read previous discussion at ~0.10x cached input rates, rapidly amortizing the initial 1.25x cache write fee.
  - **Compact-on-Exceed**: When accumulated tokens cross the session watermark, older intermediate proposals are collapsed into compact pointers (`[Previous Round: path, verdict, summary]`), preserving the refactoring trajectory while keeping active working context lean.

---

## 3. The BookKeeper Dilemma & 3-Tier Adaptive Retrieval

### Q1. Why can't `query_knowledge` just be a simple tool? Why involve an agent?
- **The Dilemma**:
  - In coding tasks, agents frequently execute 3 to 4 API queries concurrently via parallel tool calling. Launching an LLM agent for every single lookup creates unacceptable latency ($1\sim 3\text{s}$) and token waste.
  - However, pure deterministic matching fails on symptom-based queries (e.g., *"why does my button blink on hover?"*), multi-hop reasoning across leaves, or subtle synonym aliasing.

### Q2. Why a 3-tier effort model instead of a single retrieval strategy?
- **Resolution**:
  - Rather than forcing a compromise between speed and depth, `query_knowledge` exposes three explicit effort levels:
    - **`low` (Deterministic Fast-Path)**: Local Vector/FTS search ($<50\text{ms}$, 0 LLM cost). Fails fast if confidence $< \tau_{\text{low}}$ (no LLM fallback). Designed for high-volume parallel batch lookups.
    - **`medium` (Optimistic Fast-Path with Gated Escalation — Default)**: Handles ~80% of queries directly via local search in $<50\text{ms}$. If confidence falls below $\tau_{\text{med}}$, automatically escalates to `BookKeeperAgent` (`gpt-4o-mini`) for query expansion and cross-checks.
    - **`high` (Deep Agent Exploration with Initial Vector Match)**: Executes an initial single-vector search first and passes the candidate results to `BookKeeperAgent` (`gpt-4o-mini`) for deep multi-hop synthesis, query decomposition, and cross-domain triage.
  - *(See [architecture.md#51-query_knowledge-adaptive-knowledge-retrieval-tool](architecture.md#51-query_knowledge-adaptive-knowledge-retrieval-tool) for parameters and thresholds).*

### Q3. Why seed `BookKeeperAgent` with initial vector results on `high` effort?
- **Analysis**:
  - Completely bypassing the local index forces `BookKeeperAgent` to start cold, requiring blind exploratory tool calls to locate relevant branches.
  - Passing through the initial vector search results provides an immediate topological anchor and candidate snippets. `BookKeeperAgent` can then focus on multi-hop cross-referencing, synonym expansion, and disambiguation rather than initial discovery.

---

## 4. Context Isolation and Prompt Caching Optimization

### Q1. Is Zero-Context isolation outside `TaskSolverAgent` viable?
- **Analysis**:
  - Running subagents without parent conversational history eliminates massive token transfer and keeps subagents focused.
  - **Risk**: Pronoun and co-reference ambiguity (e.g., *"apply a11y to that button"* fails in isolation).
- **Resolution**:
  - `TaskSolverAgent` is instructed to formulate **self-contained, disambiguated queries** before calling tools (e.g., `query_knowledge(query="HTML custom button ARIA role keyboard accessibility", effort="medium")`).
  - `BookKeeperAgent` operates in a stateless sandbox with zero historical baggage.

### Q2. Sliding Windows vs. Linear Context with Lazy Compaction
- **Flaw of Sliding Windows / Mid-Session Summaries**:
  - Modern LLM prompt caching (e.g., OpenAI Prompt Caching) relies on **static prefix matching** for 50–80% cost discounts and near-instant TTFT.
  - Shifting windows or summarizing context mid-session alters the prefix on every turn, destroying the cache.
- **LibHippo's 3-Zone Strategy**:
  - **Zone 1 (Immutable Prefix)**: System prompt, static user preferences, and catalog spec remain constant (100% cache hit rate).
  - **Zone 2 (Append-Only Linear History)**: Turns, reasoning traces, and retrieved snippets append sequentially, preserving previous KV-cache.
  - **Zone 3 (Lazy Compaction)**: Triggered only at high token watermarks (e.g., 8,000 tokens). Evicts bulky past tool outputs by replacing raw snippets with concise markers (`[Referenced: common/.../syntax.md]`) while preserving reasoning traces.
  - *(See [architecture_runner.md](architecture_runner.md) for complete runner lifecycle, zone definitions, and eviction data structures).*

### Q3. Why disable prompt cache writes on single-use lookups (BookKeeper) while enabling them for Check-Verify refactoring loops?
- **The Financial Dilemma**:
  - OpenAI applies a cache write surcharge (~1.25x the standard input rate) when populating the prompt cache, expecting savings on subsequent cache reads (~0.1x).
  - A subagent operating on strictly one-off tasks (e.g. `BookKeeperAgent` in its **Zero-Context Sandbox** or one-shot web scraping) never re-reads its prompt; paying the 1.25x surcharge for single-use queries is strictly wasteful.
- **The Resolution**:
  - **Cache Write: DISABLED** on `BookKeeperAgent` (and one-off `CuratorAgent` scrapes): Single-use prompts are billed at the standard baseline input rate, avoiding the 1.25x write surcharge on prompts that will never be re-read.
  - **Cache Write: ENABLED** on:
    1. `TaskSolverAgent`: Repeated conversational turns and iterative problem solving.
    2. **Check $\rightarrow$ Verify Refactoring Context Loop** (`VerifierAgent` and `CuratorAgent` during refactoring cycles): Sequential turns append linearly, allowing subsequent rounds of split planning, drafting, and re-auditing to read cached prefixes at ~0.10x cost, rapidly amortizing the initial write fee.

---

## 5. Model Selection Rationale & Verification Benchmarks

### 5.1 Model Tiering Philosophy
LibHippo matches model tiers to task complexity, latency constraints, and operational cost:
- **User-Configurable Flagship (`TaskSolverAgent`)**: Selected by the user via UI/CLI according to task complexity and cost preference.
- **High-Order Refactoring Authority (`gpt-6.1-sol` for `VerifierAgent`)**: Delivers near-Astra architectural reasoning for complex hierarchy partitioning and disk mutations at $2.00 / $10.00, completely bypassing the prohibitive cost of the `o1` series ($15.00 / $60.00).
- **High-Efficiency Web Draftsman (`gpt-6-luna` for `CuratorAgent`)**: Leverages a 1.05M token context window at $0.10 / $0.50 to ingest whole API documentation pages without truncation.
- **Ultra-Fast Sub-Second Librarian (`gpt-5-nano` for `BookKeeperAgent`)**: Optimized for high-throughput query expansion and snippet extraction at $0.05 / $0.40, maintaining sub-300ms interactive retrieval response times.
- **Deterministic Typed Gatekeeper (`TypeSafe Jev` for `CheckerAgent`)**: Evaluates taxonomy, sizing hysteresis, importance/effectiveness, and content quality deterministically with $0 LLM token cost.
*(See [architecture.md#3-agent-specifications-models-and-context-isolation](architecture.md#3-agent-specifications-models-and-context-isolation) for full agent configuration matrix).*

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
- **Evaluation Criteria**: Correctly documents breaking changes and return signatures, structuring the output strictly according to the Hub-Leaf markdown template.

#### Benchmark 3: VerifierAgent Evaluation (Strict Deprecation Detection)
```text
Context: You are a strict code quality auditor. Project convention requires React 19 standard compliance.
Inspect the following code draft submitted by TaskSolverAgent:

```javascript
const [state, formAction] = useFormState(action, null);
```

Task: Decide whether to APPROVE or REVISE. Provide actionable technical justification.
```
- **Evaluation Criteria**: Immediately flags `useFormState` as deprecated in React 19, instructs replacement with `useActionState`, and outputs `REVISE` with clear technical rationale.
