import React, { useState, useEffect } from "react";
import {
  Activity,
  Layers,
  Zap,
  RotateCcw,
  Minimize2,
  CheckCircle2,
  AlertTriangle,
  FolderGit2,
} from "lucide-react";
import { LifecyclePhase } from "../../types/events";

interface HeaderProps {
  connected: boolean;
  projectId: string;
  conversationId: string;
  currentPhase: LifecyclePhase;
  totalTokens: number;
  onReset: () => void;
  onCompact: () => void;
}

const PHASES: { id: LifecyclePhase; label: string }[] = [
  { id: "alignment", label: "Alignment" },
  { id: "planning", label: "Planning" },
  { id: "implementation", label: "Coding" },
  { id: "review", label: "Review" },
  { id: "maintenance", label: "Maintenance" },
];

export const Header: React.FC<HeaderProps> = ({
  connected,
  projectId,
  conversationId,
  currentPhase,
  totalTokens,
  onReset,
  onCompact,
}) => {
  const [mode, setMode] = useState<string>("default");

  useEffect(() => {
    fetch("/api/settings/runtime")
      .then((res) => res.json())
      .then((data) => {
        if (data.mode) setMode(data.mode);
      })
      .catch((e) => console.error(e));
  }, []);

  const handleModeChange = async (newMode: string) => {
    setMode(newMode);
    await fetch("/api/settings/runtime", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode: newMode }),
    });
  };

  const softLimit = 60000;
  const hardLimit = 100000;
  const tokenPercentage = Math.min(100, Math.round((totalTokens / hardLimit) * 100));

  return (
    <header className="h-14 border-b border-slate-800 bg-[#0c1222] px-4 flex items-center justify-between gap-4 text-xs select-none">
      {/* Left: Project & Session */}
      <div className="flex items-center gap-3">
        <div className="flex items-center gap-2 font-mono font-semibold text-slate-200">
          <FolderGit2 className="w-4 h-4 text-sky-400" />
          <span>{projectId || "libhippo"}</span>
        </div>
        <span className="text-slate-600">/</span>
        <span className="font-mono text-slate-400 text-[11px] truncate max-w-[120px]">
          {conversationId || "session"}
        </span>
        <div
          className={`w-2 h-2 rounded-full ${
            connected ? "bg-emerald-500 shadow-[0_0_8px_rgba(16,185,129,0.6)]" : "bg-rose-500"
          }`}
          title={connected ? "WebSocket Connected" : "Connecting..."}
        />
      </div>

      {/* Center: 5-Phase Breadcrumb Trail */}
      <div className="hidden md:flex items-center gap-1.5 bg-slate-900/80 px-2 py-1 rounded-full border border-slate-800/80">
        {PHASES.map((p, idx) => {
          const isCurrent = currentPhase === p.id;
          const isPassed = PHASES.findIndex((x) => x.id === currentPhase) > idx;

          return (
            <div key={p.id} className="flex items-center">
              <div
                className={`px-2.5 py-0.5 rounded-full flex items-center gap-1 transition-all ${
                  isCurrent
                    ? "bg-sky-500/20 text-sky-300 font-semibold border border-sky-500/40"
                    : isPassed
                    ? "text-emerald-400"
                    : "text-slate-500"
                }`}
              >
                {isPassed ? (
                  <CheckCircle2 className="w-3 h-3 text-emerald-400" />
                ) : isCurrent ? (
                  <Activity className="w-3 h-3 text-sky-400 animate-pulse" />
                ) : null}
                <span>{p.label}</span>
              </div>
              {idx < PHASES.length - 1 && (
                <span className="text-slate-700 mx-1">›</span>
              )}
            </div>
          );
        })}
      </div>

      {/* Right: Token Gauge, Autonomy Mode, Actions */}
      <div className="flex items-center gap-3">
        {/* Token Gauge */}
        <div className="hidden lg:flex items-center gap-2 bg-slate-900 px-2.5 py-1 rounded border border-slate-800">
          <Layers className="w-3.5 h-3.5 text-slate-400" />
          <div className="flex flex-col gap-0.5">
            <div className="flex justify-between items-center text-[10px] text-slate-400 font-mono">
              <span>{totalTokens.toLocaleString()} tokens</span>
              <span className={totalTokens >= softLimit ? "text-amber-400 font-semibold" : ""}>
                {tokenPercentage}%
              </span>
            </div>
            <div className="w-24 h-1.5 bg-slate-800 rounded-full overflow-hidden">
              <div
                className={`h-full transition-all ${
                  totalTokens >= hardLimit
                    ? "bg-rose-500"
                    : totalTokens >= softLimit
                    ? "bg-amber-400"
                    : "bg-sky-500"
                }`}
                style={{ width: `${tokenPercentage}%` }}
              />
            </div>
          </div>
        </div>

        {/* Compact Button */}
        <button
          onClick={onCompact}
          className="p-1.5 rounded hover:bg-slate-800 text-slate-400 hover:text-slate-200 transition"
          title="Compact Zone 3 Context Memory"
        >
          <Minimize2 className="w-3.5 h-3.5" />
        </button>

        {/* Mode Selector */}
        <div className="flex items-center gap-1 bg-slate-900 border border-slate-800 rounded px-1.5 py-0.5">
          <Zap className="w-3 h-3 text-amber-400" />
          <select
            value={mode}
            onChange={(e) => handleModeChange(e.target.value)}
            className="bg-transparent text-slate-200 text-xs font-mono outline-none cursor-pointer"
          >
            <option value="turbo">turbo</option>
            <option value="default">default</option>
            <option value="request_review">request_review</option>
          </select>
        </div>

        {/* Reset Conversation */}
        <button
          onClick={onReset}
          className="flex items-center gap-1 px-2 py-1 rounded bg-slate-800/80 hover:bg-slate-800 text-slate-300 hover:text-white transition"
          title="Reset active session"
        >
          <RotateCcw className="w-3 h-3" />
          <span className="hidden sm:inline">Reset</span>
        </button>
      </div>
    </header>
  );
};
