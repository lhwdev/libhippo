import React, { useState } from "react";
import { Compass, Send, Zap, Clock } from "lucide-react";

interface SidecarDrawerProps {
  messages: Array<{ query: string; response: string; timestamp: string }>;
  onSendQuery: (query: string) => void;
}

export const SidecarDrawer: React.FC<SidecarDrawerProps> = ({ messages, onSendQuery }) => {
  const [query, setQuery] = useState("");

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!query.trim()) return;
    onSendQuery(query.trim());
    setQuery("");
  };

  return (
    <div className="flex-1 flex flex-col bg-[#090e1a] overflow-hidden text-xs">
      {/* Header Banner */}
      <div className="p-3 border-b border-slate-800 bg-[#0d1424] flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Compass className="w-4 h-4 text-sky-400" />
          <span className="font-semibold text-slate-200">Ephemeral Sidecar Agent (/btw)</span>
        </div>
        <div className="flex items-center gap-1.5 text-[11px] text-emerald-400 font-mono bg-emerald-500/10 px-2 py-0.5 rounded border border-emerald-500/20">
          <Zap className="w-3 h-3" />
          <span>100% KV-Cache Read Hit</span>
        </div>
      </div>

      <div className="p-3 bg-slate-900/40 border-b border-slate-800/80 text-[11px] text-slate-400">
        Inquire about current code decisions, errors, or files without interrupting the primary agent's active execution plan.
      </div>

      {/* Messages Scroll Area */}
      <div className="flex-1 overflow-y-auto p-4 space-y-3">
        {messages.length === 0 && (
          <div className="h-full flex flex-col items-center justify-center text-center text-slate-500">
            <Compass className="w-8 h-8 text-slate-700 mb-2" />
            <p className="font-medium text-slate-400">No sidecar inquiries yet.</p>
            <p className="text-[11px] text-slate-600 max-w-xs mt-1">
              Ask questions like &quot;Why did we choose SQLite over Postgres?&quot; or &quot;Which test failed?&quot;
            </p>
          </div>
        )}

        {messages.map((item, idx) => (
          <div key={idx} className="space-y-1.5 p-3 rounded-lg bg-[#0d1424] border border-slate-800">
            <div className="flex items-center justify-between font-mono text-[11px]">
              <span className="text-sky-400 font-semibold">Q: {item.query}</span>
              <span className="flex items-center gap-1 text-slate-500">
                <Clock className="w-3 h-3" />
                <span>{item.timestamp}</span>
              </span>
            </div>
            <div className="text-slate-200 leading-relaxed whitespace-pre-wrap pl-2 border-l-2 border-emerald-500/40">
              {item.response}
            </div>
          </div>
        ))}
      </div>

      {/* Query Input */}
      <form onSubmit={handleSubmit} className="p-3 border-t border-slate-800 bg-[#0c1222] flex items-center gap-2">
        <input
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Ask sidecar a quick question (/btw)..."
          className="flex-1 bg-slate-950 border border-slate-800 focus:border-sky-500 rounded px-3 py-2 text-slate-100 text-xs outline-none"
        />
        <button
          type="submit"
          disabled={!query.trim()}
          className="flex items-center gap-1 px-3 py-2 rounded bg-sky-600 hover:bg-sky-500 disabled:opacity-40 text-white font-medium transition"
        >
          <Send className="w-3.5 h-3.5" />
          <span>Ask</span>
        </button>
      </form>
    </div>
  );
};
