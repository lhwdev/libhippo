import React, { useState, useEffect } from "react";
import { Database, Search, RefreshCw, FileText, CheckCircle2, AlertCircle, BookOpen } from "lucide-react";
import { KnowledgeMount } from "../../types/api";
import { MarkdownRenderer } from "../common/MarkdownRenderer";

interface KnowledgeDocItem {
  path: string;
  title: string;
  namespace: string;
  version?: string;
}

export const KnowledgeExplorer: React.FC = () => {
  const [mounts, setMounts] = useState<KnowledgeMount[]>([]);
  const [documents, setDocuments] = useState<KnowledgeDocItem[]>([]);
  const [selectedDocPath, setSelectedDocPath] = useState<string | null>(null);
  const [selectedDocContent, setSelectedDocContent] = useState<string | null>(null);
  const [loadingDoc, setLoadingDoc] = useState(false);

  const [query, setQuery] = useState("");
  const [effort, setEffort] = useState<"low" | "medium" | "high">("medium");
  const [criticality, setCriticality] = useState<"mandatory" | "preferred" | "optional">("preferred");
  const [loadingQuery, setLoadingQuery] = useState(false);
  const [queryResult, setQueryResult] = useState<any>(null);
  const [statusMsg, setStatusMsg] = useState<string | null>(null);
  const [docFilter, setDocFilter] = useState("");

  const loadMounts = () => {
    fetch("/api/knowledge/mounts")
      .then((res) => res.json())
      .then((data) => setMounts(data.mounts || []))
      .catch((err) => console.error(err));
  };

  const loadDocuments = () => {
    fetch("/api/knowledge/documents")
      .then((res) => res.json())
      .then((data) => {
        setDocuments(data.documents || []);
        if (data.documents && data.documents.length > 0 && !selectedDocPath) {
          handleSelectDoc(data.documents[0].path);
        }
      })
      .catch((err) => console.error(err));
  };

  useEffect(() => {
    loadMounts();
    loadDocuments();
  }, []);

  const handleSelectDoc = async (path: string) => {
    setSelectedDocPath(path);
    setLoadingDoc(true);
    setQueryResult(null); // Switch view to document
    try {
      const res = await fetch(`/api/knowledge/document?path=${encodeURIComponent(path)}`);
      const data = await res.json();
      if (data.content) {
        setSelectedDocContent(data.content);
      } else {
        setSelectedDocContent(`[Error: Document '${path}' could not be loaded]`);
      }
    } catch (err) {
      setSelectedDocContent(`[Error loading document: ${String(err)}]`);
    } finally {
      setLoadingDoc(false);
    }
  };

  const handleExecuteQuery = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!query.trim()) return;

    setLoadingQuery(true);
    setSelectedDocPath(null);
    setSelectedDocContent(null);
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
      setLoadingQuery(false);
    }
  };

  const handleRebuild = async () => {
    setStatusMsg("Compacting vector indices and running maintenance...");
    try {
      await fetch("/api/knowledge/rebuild", { method: "POST" });
      setStatusMsg("Maintenance & index compaction completed.");
      loadMounts();
      loadDocuments();
      setTimeout(() => setStatusMsg(null), 3000);
    } catch (err) {
      console.error(err);
    }
  };

  const filteredDocs = documents.filter((d) =>
    d.path.toLowerCase().includes(docFilter.toLowerCase()) ||
    (d.title && d.title.toLowerCase().includes(docFilter.toLowerCase()))
  );

  return (
    <div className="flex-1 flex flex-col md:flex-row bg-[#090e1a] overflow-hidden text-xs">
      {/* Left: Mounts, Documents & Controls */}
      <div className="w-full md:w-80 border-r border-slate-800 p-4 space-y-4 bg-[#0c1222] overflow-y-auto flex flex-col">
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

        <div className="space-y-1.5">
          {mounts.map((m) => (
            <div key={m.namespace} className="p-2 rounded bg-slate-900/60 border border-slate-800 space-y-0.5">
              <div className="flex items-center justify-between font-mono text-[11px]">
                <span className="font-semibold text-sky-300">{m.namespace}/</span>
                <span className={`px-1.5 rounded text-[9px] ${m.read_only ? "bg-amber-500/20 text-amber-400" : "bg-emerald-500/20 text-emerald-400"}`}>
                  {m.read_only ? "RO" : "RW"}
                </span>
              </div>
              <div className="font-mono text-[10px] text-slate-500 truncate" title={m.physical_path}>
                {m.physical_path}
              </div>
            </div>
          ))}
        </div>

        {/* Documents Browser */}
        <div className="pt-2 border-t border-slate-800 space-y-2">
          <div className="flex items-center justify-between">
            <span className="font-semibold text-slate-200 flex items-center gap-1.5">
              <FileText className="w-3.5 h-3.5 text-sky-400" />
              <span>Documents ({documents.length})</span>
            </span>
          </div>
          <input
            type="text"
            value={docFilter}
            onChange={(e) => setDocFilter(e.target.value)}
            placeholder="Filter documents..."
            className="w-full px-2 py-1 bg-slate-950 border border-slate-800 rounded text-slate-200 text-[11px] outline-none focus:border-sky-500"
          />
          <div className="max-h-48 overflow-y-auto space-y-1">
            {filteredDocs.map((doc) => (
              <button
                key={doc.path}
                type="button"
                onClick={() => handleSelectDoc(doc.path)}
                className={`w-full text-left p-1.5 rounded border transition flex flex-col ${
                  selectedDocPath === doc.path
                    ? "bg-sky-500/15 border-sky-500/40 text-sky-200"
                    : "bg-slate-900/50 border-slate-800/80 text-slate-400 hover:text-slate-200 hover:bg-slate-800/50"
                }`}
              >
                <span className="font-medium truncate text-[11px] text-slate-200">{doc.title || doc.path}</span>
                <span className="font-mono text-[9px] text-slate-500 truncate">{doc.path}</span>
              </button>
            ))}
          </div>
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
              rows={2}
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="e.g. 'Authentication token validation pattern'..."
              className="w-full p-2 bg-slate-950 border border-slate-800 rounded text-slate-200 text-xs outline-none resize-none focus:border-sky-500"
            />
          </div>

          <button
            type="submit"
            disabled={!query.trim() || loadingQuery}
            className="w-full py-1.5 bg-sky-600 hover:bg-sky-500 disabled:opacity-40 text-white font-medium rounded transition text-xs"
          >
            {loadingQuery ? "Searching Knowledge..." : "Execute query_knowledge"}
          </button>
        </form>
      </div>

      {/* Right: Markdown Document or Results View */}
      <div className="flex-1 flex flex-col p-6 bg-[#080d19] overflow-y-auto space-y-4">
        {selectedDocPath && selectedDocContent ? (
          <div className="space-y-3">
            <div className="flex items-center justify-between pb-2 border-b border-slate-800 font-mono text-[11px]">
              <div className="flex items-center gap-2">
                <BookOpen className="w-4 h-4 text-sky-400" />
                <span className="text-slate-300 font-semibold">{selectedDocPath}</span>
              </div>
              <span className="text-slate-500 text-[10px]">Markdown View</span>
            </div>
            {loadingDoc ? (
              <div className="text-slate-500 py-8 text-center">Loading document...</div>
            ) : (
              <div className="p-4 rounded-lg bg-[#0c1222] border border-slate-800/80">
                <MarkdownRenderer content={selectedDocContent} />
              </div>
            )}
          </div>
        ) : queryResult ? (
          <div className="space-y-4">
            <div className="flex items-center justify-between pb-2 border-b border-slate-800">
              <h3 className="font-semibold text-slate-200 text-sm">
                Retrieval Results
              </h3>
              <div className="flex items-center gap-3 font-mono text-[11px]">
                <span className="text-slate-400">Duration: <strong className="text-sky-300">{queryResult.duration_ms} ms</strong></span>
                <span className="text-slate-400">Tier: <strong className="text-slate-200">{queryResult.effort}</strong></span>
                <span className="text-slate-400">Criticality: <strong className="text-slate-200">{queryResult.criticality}</strong></span>
              </div>
            </div>

            {queryResult.result?.content ? (
              <div className="space-y-2">
                <div className="flex items-center gap-2 text-emerald-400 font-mono text-xs">
                  <CheckCircle2 className="w-4 h-4" />
                  <span>HIT: {queryResult.result.path || "Knowledge Document"}</span>
                </div>
                <div className="p-4 rounded-lg bg-[#0c1222] border border-slate-800/80">
                  <MarkdownRenderer content={queryResult.result.content} />
                </div>
              </div>
            ) : queryResult.result?.retrieved_nodes && queryResult.result.retrieved_nodes.length > 0 ? (
              <div className="space-y-3">
                <div className="flex items-center gap-2 text-emerald-400 font-mono text-xs">
                  <CheckCircle2 className="w-4 h-4" />
                  <span>Retrieved {queryResult.result.retrieved_nodes.length} Node(s)</span>
                </div>
                {queryResult.result.retrieved_nodes.map((node: any, idx: number) => (
                  <div key={idx} className="p-4 rounded-lg bg-[#0c1222] border border-slate-800/80 space-y-2">
                    <div className="font-mono text-sky-300 font-medium text-xs pb-1 border-b border-slate-800/60">
                      {node.path}
                    </div>
                    <MarkdownRenderer content={node.content} />
                  </div>
                ))}
              </div>
            ) : (
              <div className="space-y-2">
                <div className="flex items-center gap-2 text-amber-400 font-mono text-xs">
                  <AlertCircle className="w-4 h-4" />
                  <span>Status: {queryResult.result?.status || "MISS"}</span>
                </div>
                <pre className="p-3 bg-slate-950 border border-slate-800 rounded font-mono text-[11px] text-slate-300 overflow-x-auto whitespace-pre-wrap">
                  {JSON.stringify(queryResult.result, null, 2)}
                </pre>
              </div>
            )}
          </div>
        ) : (
          <div className="h-64 flex flex-col items-center justify-center text-slate-500">
            <BookOpen className="w-8 h-8 text-slate-700 mb-2" />
            <p>Select a document from the list or run a query from the testbench to view formatted knowledge markdown.</p>
          </div>
        )}
      </div>
    </div>
  );
};
