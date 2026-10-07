# LibHippo Architecture

> This is summary to other documents; all text should be kept short and concise.

LibHippo is an autonomous coding agent framework built on AutoGen 0.4. It decouples into two core pillars:

1. **[Knowledge Management Subsystem](architecture_knowledge.md)** (`libhippo.storage`, `libhippo.tools`, `libhippo.agents`): General-purpose, permanent knowledge library for LLM.
2. **[General Agent Harness](architecture_harness.md)** (`libhippo.runner`, `libhippo.web`): Multi-zone memory, tool execution runtime, targeted WebSockets with delta chaining, and interactive web interface.

For design rationale, trade-offs, and benchmarks, refer to [design_rationale_and_qa.md](design_rationale_and_qa.md).

---

## 1. Knowledge Management Subsystem

Maintains living, audited engineering knowledge across agent sessions.

- **Storage (`libhippo.storage`)**:
  - Cascading tree namespaces: `project/` (repository rules), `common/` (shared idioms), `user/` (preferences), `plugins/` (external tools).
  - Markdown files serve as the human-auditable source of truth (`source`, `version_check`).
  - Local vector store (SQLite-vec / ChromaDB) accelerates coarse/fine semantic queries.
  - Hub (summaries) and Leaf (detailed rules) layout eliminates runtime summarization costs.
  - Automated Freshness Checker: SQLite-tracked `last_checked_at` and `freshness_status` without git churn; zero-token deterministic registry/terminal checking; bi-directional parent/child cascading.
- **3-Tier Adaptive Retrieval (`query_knowledge`)**:
  - `effort=low`: Vector/FTS search ($\text{confidence} \ge \tau_{\text{low}}$, defined by `DEFAULT_THRESHOLD_LOW`). Returns `content` on hit; fails fast without LLM calls on miss.
  - `effort=medium`: Vector search guarded by `BookKeeperAgent` (TypeSafe Jev) to prevent coarseness mismatches; escalates to Curator on `MISS:GAP`.
  - `effort=high`: Broad vector seed + deep `BookKeeperAgent` exploration. Falls back to web research if criticality is mandatory.
  - `criticality`: `mandatory` (obligatory curation on miss), `preferred` (fallback to model weights, 0 web calls), `optional` (fail fast).
  - **Staleness Routing**: `optional`/`low` (No Fetch with advisory), `preferred` (Stale-While-Revalidate in background), `mandatory` + `high` (Wait for Latest). Forced updates centralized in `modify_knowledge(action="revalidate")`.

- **Agents Orchestration**: `GraphFlow`
  - `TaskSolverAgent` is related to general agent harness.
  - `BookKeeperAgent`: Powered by Jev, inspects retrieval candidate, triggers `CuratorAgent` on `MISS:GAP`, and asynchronously maintains decayed vector search aliases.
  - **Maker-Checker Governance Loop (`libhippo.orchestration.maker_checker`)**

    1. `CuratorAgent` as maker generates candidate markdown drafts from web specifications upon mandatory retrieval misses.
    2. `CheckerAgent` (TypeSafe Jev) as checker evaluates frontmatter, length bounds with hysteresis, content quality, and rule effectiveness and invokes `CuratorAgent` (for low quality) or `VerifierAgent` (for merge/split) if needed.
    3. `VerifierAgent` as verifier handles escalations (splitting oversized nodes, merging undersized stubs) and holds exclusive disk write authority (`modify_knowledge`).
  - **Context Knowledge Harvesting (`KnowledgeHarvestObserver`, `KnowledgeHarvestSidecar`)**:
    - `KnowledgeHarvestObserver` (TypeSafe Jev System One) evaluates completed turns/tasks for novel patterns and bug resolutions with zero LLM reasoning cost.
    - `KnowledgeHarvestSidecar` synthesizes drafts from warm KV-cache snapshots, submits to Maker-Checker governance, and commits without blocking interactive turns.
    - `TaskSolverAgent` can also explicitly queue discoveries via `record_learning(topic, insight, scope)`.

---

## 2. General Agent Harness

Provides an execution runtime for autonomous software engineering tasks.

- **Multi-Zone Memory & Workload Governance (`libhippo.runner.memory`, `governor`)**:
  - **Zone 1 (Static Prefix)**: System prompt, security policies, and tool definitions (100% KV-cache warm).
  - **Zone 2 (Linear History)**: Conversation turns tagged with temporal metadata (`<USER_PROMPT timestamp=...>`).
  - **Zone 3 (Compacted Tool Outputs)**: Evictable execution logs deterministically compacted to reference pointers near context limits.

- **Tool Suite (`libhippo.runner.tools`)**:
  - **Filesystem**: `read_file` (windowed, max 800 lines), `write_file` (targeted line/string replace), `overwrite_file`, `delete_file`.
  - **Code Exploration**: Pure Python `search_file` (hierarchical `.gitignore` parsing) and `list_dir`.
  - **Terminal & Tasks**: Sandboxed `run_command` and background `manage_task` execution.
  - **Knowledge**: `query_knowledge` (mandatory retrieval) and `refine_knowledge`, `record_learning` (learning queues), and `modify_knowledge`.
  - **Subagents**: `invoke_subagent`, `shorten_tool_output`, and `manage_subagents` for parallel delegation and LLM-driven output shortening.
  - **User Interaction**: Interactive `ask_question` modal and ambient `get_status`.

- **Targeted Transport Architecture (`libhippo.runner.transport`)**:
  - **Dedicated WebSockets**: Uses `previous_response_id` for delta chaining and native mid-turn steering.
  - **Resilient HTTP Fallback**: Automatic failover to `OpenAIResponsesClient` on socket drop.
  - **Ephemeral Sidecar (`/btw`) & Harvest Sidecar**: Parallel, non-interfering workers hitting warm parent KV-cache.
  - **Cache & Usage Telemetry**: Deep extraction of `input_tokens_details.cached_tokens`, `cache_write_tokens`, and `reasoning_tokens` for live hit rate logging (`Cache: HIT (...)`).

- **Client Decoupling**:
  - Headless backend engine (`GeneralAgentHarness`) streaming typed events (`TokenChunkEvent`, `ToolCallStartEvent`, `ToolCallResultEvent`, `TurnCompletedEvent` with cache metrics).
  - React 19 web frontend (`frontend/web`) and aiohttp server (`src/libhippo/web`).

---

## 3. Subsystem Architecture

```mermaid
flowchart TD
    subgraph Harness["General Agent Harness (architecture_harness.md)"]
        H[GeneralAgentHarness] --> G[Workload Governor & Multi-Zone Memory]
        H --> TS[TaskSolverAgent]
        H --> SC["/btw Sidecar Agent"]
        H --> Web["Web UI / CLI Transport"]
        TS -->|record_learning| HS[KnowledgeHarvestSidecar]
        H -->|post_task_maintenance| HO[KnowledgeHarvestObserver / Jev]
        HO -->|has_novel_learnings >= 0.70| HS
    end

    subgraph Knowledge["Knowledge Subsystem (architecture_knowledge.md)"]
        TS -->|Mandatory query_knowledge| QD[3-Tier Dispatcher]
        QD --> KS[(Knowledge Store<br>Markdown + Vector)]
        QD --> BK[BookKeeperAgent]
        QD -->|MISS:MANDATORY| CU[CuratorAgent]
        CU --> CH[CheckerAgent / Jev]
        HS -->|Draft candidate| CH
        CH -->|Escalate| VA[VerifierAgent]
        CH -->|PASS| KS
        VA -->|modify_knowledge| KS
    end

    TS -.->|Post-Task Catalog Sync| KS
```

---

## Documents

- [Knowledge Architecture (`architecture_knowledge.md`)](architecture_knowledge.md): Knowledge tree schema, 3-tier retrieval, and Maker-Checker contracts.
- [Harness Architecture (`architecture_harness.md`)](architecture_harness.md): Agent harness, multi-zone memory, tools, and network transport.
- [Design Rationale & Q&A Log (`design_rationale_and_qa.md`)](design_rationale_and_qa.md): Trade-offs, design dilemmas, derivations, and benchmarks.
