import React, { useState, useEffect } from "react";
import { Database, Search, RefreshCw, CheckCircle, FolderTree, Cpu } from "lucide-react";
import { KnowledgeMount } from "../../types/api";

export const KnowledgeExplorer: React.FC = () => {
  const [mounts, setMounts] = useState<KnowledgeMount[]>([]);
  const [query, setQuery] = useState("");
  const [effort, setEffort] = useState<"low" | "medium" | "high">("medium");
  const [criticality, setCriticality] = useState<"mandatory" | "preferred" | "optional">("preferred");
  const [loading, setLoading] = useState(false);
  const [queryResult, setQueryResult] = useState<any>(null);
  const [statusMsg, setStatusMsg] = useState<string | null>(null);

  useEffect(() => {
    fetch("/api/knowledge/mounts")
      .then((res) => res.json())
      .then((data) => setMounts(data.mounts || []))
      .catch((err) => console.error(err));
  }, []);

  const handleExecuteQuery = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!query.trim()) return;

    setLoading(true);
    try {
      const res = await fetch("/api/knowledge/query", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query: query.trim(), effort, criticality }),
      });
      const data = await res.json();
      setQueryResult(data);
    } catch (err) {
      console.error(err);
    } finally {
      setLoading(false);
    }
  };

  const handleRebuild = async () => {
    setStatusMsg("Compacting vector indices and running maintenance...");
    try {
      await fetch("/api/knowledge/rebuild", { method: "POST" });
      setStatusMsg("Maintenance & index compaction completed.");
      setTimeout(() => setStatusMsg(null), 3000);
    } catch (err) {
      console.error(err);
    }
  };

  return (
    <div className="flex-1 flex flex-col md:flex-row bg-[#090e1a] overflow-hidden text-xs">
      {/* Left: Mounts & Controls */}
      <div className="w-full md:w-80 border-r border-slate-800 p-4 space-y-4 bg-[#0c1222] overflow-y-auto">
        <div className="flex items-center justify-between pb-2 border-b border-slate-800">
          <div className="flex items-center gap-2 font-semibold text-slate-200">
            <Database className="w-4 h-4 text-sky-400" />
            <span>Mounted Namespaces</span>
          </div>
          <button
            onClick={handleRebuild}
            className="p-1 hover:bg-slate-800 rounded text-slate-400 hover:text-slate-200"
            title="Compact and rebuild vector store"
          >
            <RefreshCw className="w-3.5 h-3.5" />
          </button>
        </div>

        {statusMsg && (
          <div className="p-2 bg-emerald-500/10 border border-emerald-500/30 text-emerald-400 rounded text-[11px]">
            {statusMsg}
          </div>
        )}

        <div className="space-y-2">
          {mounts.map((m) => (
            <div key={m.namespace} className="p-2.5 rounded bg-slate-900/60 border border-slate-800 space-y-1">
              <div className="flex items-center justify-between font-mono text-[11px]">
                <span className="font-semibold text-sky-300">{m.namespace}/</span>
                <span className={`px-1.5 rounded text-[10px] ${m.read_only ? "bg-amber-500/20 text-amber-400" : "bg-emerald-500/20 text-emerald-400"}`}>
                  {m.read_only ? "RO" : "RW"}
                </span>
              </div>
              <div className="font-mono text-[10px] text-slate-500 truncate" title={m.physical_path}>
                {m.physical_path}
              </div>
            </div>
          ))}
        </div>

        {/* Query Bench Form */}
        <form onSubmit={handleExecuteQuery} className="pt-2 border-t border-slate-800 space-y-3">
          <span className="font-semibold text-slate-200 flex items-center gap-1.5">
            <Search className="w-3.5 h-3.5 text-sky-400" />
            <span>Retrieval Testbench</span>
          </span>

          <div>
            <label className="text-[10px] uppercase font-mono text-slate-500 block mb-1">Effort Tier</label>
            <div className="grid grid-cols-3 gap-1">
              {(["low", "medium", "high"] as const).map((tier) => (
                <button
                  key={tier}
                  type="button"
                  onClick={() => setEffort(tier)}
                  className={`py-1 rounded font-mono text-[11px] capitalize border transition ${
                    effort === tier
                      ? "bg-sky-500/20 border-sky-500/50 text-white font-semibold"
                      : "bg-slate-900 border-slate-800 text-slate-400 hover:text-slate-200"
                  }`}
                >
                  {tier}
                </button>
              ))}
            </div>
          </div>

          <div>
            <label className="text-[10px] uppercase font-mono text-slate-500 block mb-1">Criticality</label>
            <div className="grid grid-cols-3 gap-1">
              {(["mandatory", "preferred", "optional"] as const).map((crit) => (
                <button
                  key={crit}
                  type="button"
                  onClick={() => setCriticality(crit)}
                  className={`py-1 rounded font-mono text-[10px] capitalize border transition ${
                    criticality === crit
                      ? "bg-purple-500/20 border-purple-500/50 text-white font-semibold"
                      : "bg-slate-900 border-slate-800 text-slate-400 hover:text-slate-200"
                  }`}
                >
                  {crit}
                </button>
              ))}
            </div>
          </div>

          <div>
            <label className="text-[10px] uppercase font-mono text-slate-500 block mb-1">Query</label>
            <textarea
              rows={3}
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="e.g. 'Authentication token validation pattern'..."
              className="w-full p-2 bg-slate-950 border border-slate-800 rounded text-slate-200 text-xs outline-none resize-none focus:border-sky-500"
            />
          </div>

          <button
            type="submit"
            disabled={!query.trim() || loading}
            className="w-full py-2 bg-sky-600 hover:bg-sky-500 disabled:opacity-40 text-white font-medium rounded transition"
          >
            {loading ? "Searching Knowledge..." : "Execute query_knowledge"}
          </button>
        </form>
      </div>

      {/* Right: Results View */}
      <div className="flex-1 flex flex-col p-4 bg-[#080d19] overflow-y-auto space-y-3">
        <h3 className="font-semibold text-slate-200 text-sm pb-2 border-b border-slate-800">
          Retrieval Results
        </h3>

        {queryResult ? (
          <div className="space-y-3">
            <div className="flex items-center gap-3 p-2 bg-slate-900/60 rounded border border-slate-800 font-mono text-[11px]">
              <span className="text-slate-400">Duration: <strong className="text-sky-300">{queryResult.duration_ms} ms</strong></span>
              <span className="text-slate-400">Tier: <strong className="text-slate-200">{queryResult.effort}</strong></span>
              <span className="text-slate-400">Criticality: <strong className="text-slate-200">{queryResult.criticality}</strong></span>
            </div>

            <pre className="p-3 bg-slate-950 border border-slate-800 rounded font-mono text-[11px] text-slate-300 overflow-x-auto whitespace-pre-wrap">
              {JSON.stringify(queryResult.result, null, 2)}
            </pre>
          </div>
        ) : (
          <div className="h-64 flex flex-col items-center justify-center text-slate-500">
            <Search className="w-8 h-8 text-slate-700 mb-2" />
            <p>Run a query from the testbench to inspect confidence score, hit/miss tags, and raw snippets.</p>
          </div>
        )}
      </div>
    </div>
  );
};
