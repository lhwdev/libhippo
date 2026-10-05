import React, { useState } from "react";
import {
  BookOpen,
  ShieldCheck,
  GitFork,
  Layers,
  ChevronDown,
  ChevronRight,
  CheckCircle2,
  Clock,
  AlertTriangle,
  XCircle,
} from "lucide-react";
import { KnowledgeAgentEvent } from "../../types/events";

interface KnowledgeAgentCardProps {
  event: KnowledgeAgentEvent;
}

export const KnowledgeAgentCard: React.FC<KnowledgeAgentCardProps> = ({ event }) => {
  const [expanded, setExpanded] = useState(false);

  const isRunning = event.status === "running";
  const isPassed = event.status === "passed" || event.status === "completed";
  const isEscalated = event.status === "escalated";
  const isFailed = event.status === "failed" || event.status === "rejected";

  const getAgentBadge = (agent: string) => {
    switch (agent) {
      case "CuratorAgent":
        return {
          icon: <BookOpen className="w-3.5 h-3.5 text-purple-400" />,
          label: "Curator",
          border: "border-purple-800/60",
          bg: "bg-purple-950/20",
          text: "text-purple-300",
        };
      case "CheckerAgent":
        return {
          icon: <ShieldCheck className="w-3.5 h-3.5 text-cyan-400" />,
          label: "Checker (Jev)",
          border: "border-cyan-800/60",
          bg: "bg-cyan-950/20",
          text: "text-cyan-300",
        };
      case "VerifierAgent":
        return {
          icon: <GitFork className="w-3.5 h-3.5 text-amber-400" />,
          label: "Verifier",
          border: "border-amber-800/60",
          bg: "bg-amber-950/20",
          text: "text-amber-300",
        };
      default:
        return {
          icon: <Layers className="w-3.5 h-3.5 text-emerald-400" />,
          label: agent || "Maker-Checker",
          border: "border-emerald-800/60",
          bg: "bg-emerald-950/20",
          text: "text-emerald-300",
        };
    }
  };

  const badge = getAgentBadge(event.agent);

  return (
    <div className={`my-1.5 rounded border ${badge.border} ${badge.bg} overflow-hidden text-xs font-mono transition-all`}>
      <div
        onClick={() => setExpanded(!expanded)}
        className="flex items-center justify-between px-3 py-2 cursor-pointer hover:bg-slate-900/60 transition"
      >
        <div className="flex items-center gap-2 overflow-hidden mr-2">
          {expanded ? (
            <ChevronDown className="w-3.5 h-3.5 text-slate-400 shrink-0" />
          ) : (
            <ChevronRight className="w-3.5 h-3.5 text-slate-400 shrink-0" />
          )}
          <span className="shrink-0">{badge.icon}</span>
          <span className={`font-semibold ${badge.text} shrink-0`}>
            {badge.label}
          </span>
          <span className="text-slate-400 font-sans text-[11px] truncate">
            {event.output_summary || event.input_summary || `${event.action} (${event.status})`}
          </span>
        </div>

        <div className="flex items-center gap-2 shrink-0 text-[11px]">
          {event.target_path && (
            <span className="px-1.5 py-0.5 rounded bg-slate-950/80 border border-slate-800 text-slate-400 text-[10px]">
              {event.target_path}
            </span>
          )}
          {isRunning ? (
            <span className="flex items-center gap-1 text-sky-400">
              <Clock className="w-3 h-3 animate-spin" />
              <span>running</span>
            </span>
          ) : isPassed ? (
            <span className="flex items-center gap-1 text-emerald-400">
              <CheckCircle2 className="w-3 h-3" />
              <span>{event.status}</span>
            </span>
          ) : isEscalated ? (
            <span className="flex items-center gap-1 text-amber-400">
              <AlertTriangle className="w-3 h-3" />
              <span>escalated</span>
            </span>
          ) : isFailed ? (
            <span className="flex items-center gap-1 text-rose-400">
              <XCircle className="w-3 h-3" />
              <span>{event.status}</span>
            </span>
          ) : (
            <span className="text-slate-400">{event.status}</span>
          )}
        </div>
      </div>

      {expanded && (
        <div className="p-3 border-t border-slate-800/80 space-y-2 bg-[#080d1a] font-sans text-slate-300">
          <div className="grid grid-cols-2 gap-2 text-[11px] font-mono">
            <div>
              <span className="text-slate-500">Agent:</span> <span className="text-slate-200">{event.agent}</span>
            </div>
            <div>
              <span className="text-slate-500">Action:</span> <span className="text-slate-200">{event.action}</span>
            </div>
            <div>
              <span className="text-slate-500">Status:</span> <span className="text-slate-200">{event.status}</span>
            </div>
            {event.timestamp && (
              <div>
                <span className="text-slate-500">Time:</span> <span className="text-slate-200">{event.timestamp}</span>
              </div>
            )}
          </div>

          {event.input_summary && (
            <div>
              <span className="text-[10px] uppercase font-semibold text-slate-500 font-mono tracking-wider">
                Input Context:
              </span>
              <p className="mt-0.5 text-xs text-slate-300 bg-slate-950 p-2 rounded border border-slate-800/60 font-mono">
                {event.input_summary}
              </p>
            </div>
          )}

          {event.output_summary && (
            <div>
              <span className="text-[10px] uppercase font-semibold text-slate-500 font-mono tracking-wider">
                Output / Verdict:
              </span>
              <p className="mt-0.5 text-xs text-slate-300 bg-slate-950 p-2 rounded border border-slate-800/60 font-mono">
                {event.output_summary}
              </p>
            </div>
          )}

          {event.details && Object.keys(event.details).length > 0 && (
            <div>
              <span className="text-[10px] uppercase font-semibold text-slate-500 font-mono tracking-wider">
                Details & Diagnostics:
              </span>
              <pre className="mt-0.5 p-2 bg-slate-950 rounded text-slate-300 text-[11px] font-mono overflow-x-auto whitespace-pre-wrap border border-slate-800/60">
                {JSON.stringify(event.details, null, 2)}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  );
};
