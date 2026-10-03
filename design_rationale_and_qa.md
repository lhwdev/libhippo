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

### Q3. Why disable prompt cache writes on single-use lookups (BookKeeper) and how can we cache ONLY system prompts?
- **The Financial Dilemma (1.25x Cache Write Fee)**:
  - For models in the GPT-5.6 family and later (including `gpt-6.1-sol` and `gpt-6-luna`), OpenAI bills cache write operations at **1.25x the standard input token rate** (+25% surcharge), while cache hits receive a significant discount (0.25x–0.50x).
  - Because prompt caching on OpenAI is automatic for prefixes $\ge 1,024$ tokens, sending one-off, volatile queries (or prompts that are never reused) incurs a wasteful 25% cache write penalty on every turn without ever recouping savings.
  - A subagent operating on strictly one-off tasks (e.g. `BookKeeperAgent` in its **Zero-Context Sandbox** or one-shot web scraping) never re-reads its query or candidate snippets; paying 1.25x for single-use tokens is financially counterproductive.
- **The Resolution: Explicit Mode & System-Prompt-Only Caching**:
  - **Cache System Prompt Only (`cache_system_prompt_only = True`)**:
    - Uses OpenAI's explicit cache control mode (`prompt_cache_options.mode = "explicit"`).
    - Places an explicit `prompt_cache_breakpoint: True` (and Anthropic `cache_control: {"type": "ephemeral"}`) strictly at the boundary of the static **System Prompt** (`messages[0]`).
    - The static system prompt is written to the cache **once** (at 1.25x) and reused across subsequent queries at discounted read rates.
  - **Sub-1k Stateless Bypass (`BookKeeperAgent`)**:
    - OpenAI prompt caching only activates when the static prefix reaches at least **1,024 tokens**.
    - For lightweight single-use lookups where the system prompt and tool definitions remain below 1,024 tokens, caching does not trigger at all, completely avoiding both the cache write operation and the 1.25x fee.
  - **Full Linear Session Caching (`cache_write = True`, `cache_system_prompt_only = False`)**:
    - **`TaskSolverAgent`**: Repeated conversational turns append linearly in Zone 2, making multi-turn caching cost-effective.
    - **Check $\rightarrow$ Verify Refactoring Context Loop** (`VerifierAgent` and `CuratorAgent` during refactoring cycles): Sequential rounds append linearly, rapidly amortizing the initial 1.25x write fee across multi-turn refactoring iterations.

### Q4. Why treat high-volume tool execution as an inline subagent fan-out rather than appending raw output into context?
- **The Context Thrashing Dilemma**:
  - Commands like `npm run check` or `pytest` often emit hundreds of lines of compiler/linter errors.
  - Blindly appending raw logs into the parent agent's context produces three catastrophic side-effects:
    1. **Context Window Saturation**: Instantly exhausts token budgets, triggering premature eviction of prior architectural reasoning.
    2. **Cognitive Thrashing**: A single agent attempting to fix 14 disparate files in one thread often hallucinates, fixes one error while breaking another, or forgets earlier requirements.
    3. **Cache Invalidation**: Bulky outputs permanently bloat the active context tail, destroying downstream KV-cache reuse.
- **The 3-Tier Output Triage Resolution**:
  - The harness intercepts tool outputs and classifies them inline (via deterministic heuristics and TypeSafe Jev):
    - `short` ($\le 30$ lines): Appended directly into Zone 2 with 0 overhead.
    - `long-unimportant`: Raw log saved out-of-band to `tasks/<task_id>.log`; only a compact 2-line summary is added to Zone 2.
    - `long-important`: Decomposed into modular subproblems $[a, b, c, ...]$ and dispatched to dedicated worker subagents.
- **Why It Maximizes KV-Cache Reuse**:
  - Each spawned subagent inherits: `[Zone 1 Static Prefix] + [Parent Context (before bulky tool output)] + [Subproblem Descriptor + Relevant Slice]`.
  - Because the shared parent history is bitwise identical and already warm in the model provider's cache, **every parallel child subagent hits the KV-cache at 100% read discount**.
- **Synthetic Context Replacement**:
  - The parent context never ingests the raw 500 lines.
  - Instead, the tool return is replaced with: `[command input] + [error overview] + [subagent resolution summaries]`.
  - The tool effectively operates as a self-contained subagent orchestrator, keeping the parent's working context clean and focused.

### Q5. Two-Stage Jev Pipeline vs. Unified Single-Stage LLM for Tool Output Triage
- **The Observation**:
  - If a tool output is classified as `long-important`, an LLM must still be invoked immediately afterward to parse the 500-line log and extract isolated subproblems $[a, b, c, ...]$.
  - If it is `long-unimportant`, an LLM is still needed if a natural-language summary is required.
  - Therefore, running Jev first risks an extra serialized network round-trip while still paying full LLM input tokens on the 500 lines.
- **Architectural Comparison**:

| Dimension | Option A: Two-Stage (Jev Gatekeeper $\rightarrow$ LLM Extractor) | Option B: Unified Single-Stage LLM (`gpt-5-nano` / Structured Output) |
| :--- | :--- | :--- |
| **Network Round Trips** | 2 sequential calls (Jev ~100ms + LLM ~600ms = ~700ms) | **1 single call** (~500ms total). Faster time-to-first-subagent. |
| **Token Cost on Long Path** | Jev ($0) + LLM reads full 500 lines. | **LLM reads full 500 lines once**, emitting tier + summary + subproblems. |
| **Semantic Coherence** | Risk of mismatch: Jev flags "separable", but LLM discovers circular type dependencies and cannot isolate them. | **Holistic judgment**: LLM decides separability *while* attempting to partition the errors into subproblems. |
| **Ideal Use Case** | When `long-unimportant` outputs are handled **heuristically with 0 LLM calls** (e.g. regex exit code + raw log pointer). | When high-quality generative summarization and structured subproblem decomposition are required (**Recommended Default**). |

### Q6. Should the Tool Triage Router receive Parent Context, and how can it reuse the Prompt Cache?
- **Does the Router Need Context?**:
  - **Yes, for Goal-Aligned Triage**: Without the parent conversation, a triage model only sees raw stacktraces. It cannot know what task the developer asked for, which files were just edited, or what constraints apply. With context, it can identify regressions introduced by recent edits and formulate precise, goal-directed subproblem prompts.
- **The Cross-Model Cache Miss Dilemma**:
  - LLM KV-caches are strictly model-specific.
  - If the parent agent runs on `gpt-6.1-sol` with 6,000 tokens of conversational context, sending that 6,000-token context to `gpt-5-nano` results in a **100% cold-cache miss**.
  - `gpt-5-nano` must re-embed all 6,000 tokens and may incur a 1.25x cache write fee for a one-off call, defeating the purpose of using a small model.
- **The Resolution: Same-Model Dispatch with Lowered Reasoning Effort**:
  - By invoking the **same model** as the parent agent (e.g. `gpt-6.1-sol`), the triage call hits the **already-warm KV cache at 100% read discount** ($0.10\times \sim 0.25\times$).
  - Only the new 500 lines of error log are processed as new tokens.
  - Runtime parameters can be dynamically down-regulated (`reasoning_effort = "low"` or `"minimal"`, `temperature = 0.0`), yielding near-instant generation with low output tokens while maintaining maximum context fidelity.
- **Stateless Exception**: If a lightweight model like `gpt-5-nano` is used, it should be kept **strictly stateless** (receiving only `[Initial Goal]` + `[Error Log]`, staying under 1,024 tokens to trigger the sub-1k stateless cache bypass).

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

### Q1. Why centralize model configuration across AutoGen OpenAI models and TypeSafe Jev in `libhippo.models.llm`?
- **The Problem**:
  - AutoGen 0.4 uses `OpenAIChatCompletionClient` with specific parameters (model, temperature, reasoning_effort, default headers for prompt caching), while `CheckerAgent` relies on `typesafe_sdk.AsyncTypeSafeClient` with different credentials and execution options.
  - Scattering model configurations across individual agent modules leads to configuration divergence, complicates mock injection during testing, and hinders centralized tuning of temperature and caching flags for the architectural models (`gpt-6.1-sol`, `gpt-6-luna`, `gpt-5-nano`, `jev`).
- **The Resolution**:
  - `ModelConfig` provides a unified declarative schema specifying model name, temperature, and cache flags for each agent role.
  - `ModelRegistry` acts as a single point of configuration and mock injection, allowing unit tests and offline environments to swap implementations seamlessly while preserving identical agent logic.

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

---

## 6. Storage Durability, Cache Synchronization, and Web Search Rationale

### Q1. Why introduce an optional `force_keep` flag?
- **The Dilemma**: Automated Maker-Checker refactoring aggressively merges undersized stubs (`MERGE_REQUIRED`) or elevates oversized nodes. When a subtree (e.g., `knowledge/common/web`) is symlinked to an external repository published on GitHub, or when a file has fixed external URL anchors, automated renaming or merging breaks symlinks and Git history.
- **The Resolution**: Setting `force_keep: true` in the frontmatter marks the document as an immutable anchor. `CheckerAgent` unconditionally preserves it (`verdict: PASS`), suppressing automated rename, split, and merge directives, while `modify_knowledge` rejects deletions and coalescing unless an explicit `force=True` flag is supplied.

### Q2. Why treat SQLite and ChromaDB strictly as disposable caches with 3-tier incremental sync?
- **The Dilemma**: Developers frequently edit markdown documents directly in editors, switch branches, or pull updates via Git. If the database is treated as the primary state, discrepancies between disk files and the DB lead to stale search results and ghost nodes.
- **The Resolution**:
  - Markdown files are the **sole source of truth**. Both `knowledge_catalog.db` and `.chromadb/` are git-ignored derived caches that can be deleted and regenerated at any time.
  - **3-Tier Incremental Sync**:
    1. *`mtime` check*: Sub-millisecond skip for untouched files without opening file handles.
    2. *SHA-256 hash check*: Distinguishes actual content edits from git checkouts or file touches, avoiding redundant, CPU-intensive vector re-embedding.
    3. *Selective update & orphan pruning*: Synchronizes only dirty files and deletes DB entries for files removed from disk.

### Q3. How does LibHippo mitigate HNSW vector index degradation under heavy mutation churn?
- **The Problem**: In HNSW vector indexes (like ChromaDB's underlying `hnswlib`), deleting or updating vectors leaves "tombstone" entries in the graph. Over extended sessions with numerous edits, tombstoned vertices degrade graph connectivity and inflate memory/disk footprint.
- **The Resolution**: Because markdown files are the authoritative source of truth, rebuilding the entire index is cheap (~1–2 seconds for thousands of chunks). LibHippo provides a clean `rebuild_index()` mechanism that resets the collection and re-indexes active documents, clearing all tombstone fragmentation without risking data loss.

### Q4. Why provide a pluggable `search_web` architecture with DuckDuckGo as default?
- **The Dilemma**: Forcing a paid or gated API key (such as Tavily or Serper) creates friction for new developers running LibHippo out of the box. Conversely, a naive scraper without search indexing cannot discover relevant URLs for arbitrary queries.
- **The Resolution**: LibHippo uses DuckDuckGo (`duckduckgo_search` / `DDGS`) as a zero-key default, while offering a pluggable interface that auto-promotes to high-precision agent search engines (such as Tavily or Brave Search) when corresponding environment variables (`TAVILY_API_KEY`, `BRAVE_API_KEY`) are present.

### Q5. Why execute Vector Store compaction (`rebuild_index`) asynchronously after a task ends rather than inline during mutations?
- **The Problem**: Rebuilding the HNSW vector index (wiping the collection and re-embedding/re-indexing all knowledge documents) takes several seconds for sizeable repositories. Triggering compaction inline inside `modify_knowledge` or during an active query would stall the agent's turn, creating disruptive latency spikes in interactive problem-solving sessions.
- **The Resolution**: `VectorKnowledgeStore` monitors mutation churn via `mutation_count` and flags when `should_rebuild()` is met (default $\ge 200$ mutations). The actual compaction is dispatched **asynchronously as a background post-task maintenance operation** once `TaskSolverRunner` completes its user response. This preserves low interactive latency while ensuring the HNSW graph remains defragmented and performant.

### Q6. Why decouple knowledge namespaces into dynamic mount points instead of a single flat directory?
- **The Dilemma**:
  - A single flat directory forces all knowledge—project-specific rules, personal developer preferences, shared language specifications, and external plugin docs—into one physical tree.
  - This causes two major structural problems:
    1. **Repository Pollution**: Project git repositories would either have to vendor standard language docs or ignore the directory entirely, preventing teams from checking project-specific architectural rules into Git.
    2. **No Multi-Project Sharing**: Every project would duplicate global standards and developer preferences.
- **The Dynamic Mount Resolution**:
  - Namespaces are mapped dynamically to their logical locations:
    - `project/` $\rightarrow$ `<workspace root>/.libhippo/` (Read/Write, committed to repo Git).
    - `common/` $\rightarrow$ `<libhippo install>/knowledge/common/` (Read/Write, maintained and updated when new framework versions release, e.g. React 19).
    - `user/` $\rightarrow$ `~/.config/libhippo/knowledge/` (Read/Write, personal across projects).
    - `plugins/` $\rightarrow$ Dynamically mounted per enabled plugin (details to be discussed later).
  - **Read-Only Protection Where Needed**: Some knowledge mounts (such as read-only vendor plugins or external documentation packs) can be flagged `read_only: true`. Any write operation (`modify_knowledge`, split, merge, purge) against a read-only mount is blocked with `ReadOnlyMountError`, prompting the agent to specialize the rule inside the writable `project/` mount instead.



