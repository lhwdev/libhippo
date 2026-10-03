import React, { useState } from "react";
import { Terminal, ChevronDown, ChevronRight, CheckCircle2, AlertCircle, Clock } from "lucide-react";

interface ToolCallCardProps {
  toolCall: {
    id: string;
    name: string;
    arguments: Record<string, any>;
    result?: any;
    error?: string;
  };
}

export const ToolCallCard: React.FC<ToolCallCardProps> = ({ toolCall }) => {
  const [expanded, setExpanded] = useState(false);

  const hasResult = toolCall.result !== undefined || toolCall.error !== undefined;
  const isError = Boolean(toolCall.error);

  return (
    <div className="my-2 rounded border border-slate-800 bg-[#0d1424] overflow-hidden text-xs font-mono">
      <div
        onClick={() => setExpanded(!expanded)}
        className="flex items-center justify-between px-3 py-2 bg-slate-900/60 cursor-pointer hover:bg-slate-900 transition"
      >
        <div className="flex items-center gap-2">
          {expanded ? <ChevronDown className="w-3.5 h-3.5 text-slate-400" /> : <ChevronRight className="w-3.5 h-3.5 text-slate-400" />}
          <Terminal className="w-3.5 h-3.5 text-sky-400" />
          <span className="font-semibold text-slate-200">{toolCall.name}</span>
          <span className="text-slate-500 text-[11px] truncate max-w-[280px]">
            {JSON.stringify(toolCall.arguments)}
          </span>
        </div>
        <div className="flex items-center gap-1.5 text-[11px]">
          {!hasResult ? (
            <span className="flex items-center gap-1 text-amber-400">
              <Clock className="w-3 h-3 animate-spin" />
              <span>running</span>
            </span>
          ) : isError ? (
            <span className="flex items-center gap-1 text-rose-400">
              <AlertCircle className="w-3 h-3" />
              <span>failed</span>
            </span>
          ) : (
            <span className="flex items-center gap-1 text-emerald-400">
              <CheckCircle2 className="w-3 h-3" />
              <span>done</span>
            </span>
          )}
        </div>
      </div>

      {expanded && (
        <div className="p-3 border-t border-slate-800/80 space-y-2 bg-[#090e1a]">
          <div>
            <span className="text-[10px] uppercase font-semibold text-slate-500 tracking-wider">Arguments:</span>
            <pre className="mt-1 p-2 bg-slate-950 rounded text-slate-300 text-[11px] overflow-x-auto whitespace-pre-wrap">
              {JSON.stringify(toolCall.arguments, null, 2)}
            </pre>
          </div>
          {hasResult && (
            <div>
              <span className="text-[10px] uppercase font-semibold text-slate-500 tracking-wider">
                {isError ? "Error Trace:" : "Result Output:"}
              </span>
              <pre
                className={`mt-1 p-2 rounded text-[11px] overflow-x-auto whitespace-pre-wrap ${
                  isError ? "bg-rose-950/40 text-rose-300 border border-rose-900/50" : "bg-slate-950 text-slate-300"
                }`}
              >
                {typeof toolCall.result === "string"
                  ? toolCall.result
                  : JSON.stringify(toolCall.error ?? toolCall.result, null, 2)}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  );
};
