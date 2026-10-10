import React, { useState, useEffect, useRef } from "react";
import {
  Activity,
  Layers,
  Zap,
  RotateCcw,
  Minimize2,
  FolderGit2,
  ChevronDown,
  Plus,
  MessageSquare,
  Trash2,
  Brain,
} from "lucide-react";
import { ConversationItem } from "../../types/api";

interface HeaderProps {
  connected: boolean;
  projectId: string;
  conversationId: string;
  conversationName?: string;
  totalTokens: number;
  currentPhase?: string;
  reasoningEffort?: string;
  onReasoningEffortChange?: (effort: string) => void;
  onReset: () => void;
  onCompact: () => void;
  onSelectConversation?: (convId: string) => void;
}

export const Header: React.FC<HeaderProps> = ({
  connected,
  projectId,
  conversationId,
  conversationName,
  totalTokens,
  reasoningEffort,
  onReasoningEffortChange,
  onReset,
  onCompact,
  onSelectConversation,
}) => {
  const [mode, setMode] = useState<string>("default");
  const [showConvDropdown, setShowConvDropdown] = useState(false);
  const [conversations, setConversations] = useState<ConversationItem[]>([]);
  const dropdownRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    fetch("/api/settings/runtime")
      .then((res) => res.json())
      .then((data) => {
        if (data.mode) setMode(data.mode);
      })
      .catch((e) => console.error(e));
  }, []);

  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target as Node)) {
        setShowConvDropdown(false);
      }
    };
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  const loadConversations = async () => {
    try {
      const res = await fetch("/api/conversations");
      if (res.ok) {
        const data = await res.json();
        setConversations(data.conversations || []);
      }
    } catch (e) {
      console.error(e);
    }
  };

  const handleToggleDropdown = () => {
    if (!showConvDropdown) {
      loadConversations();
    }
    setShowConvDropdown(!showConvDropdown);
  };

  const handleDeleteConversation = async (e: React.MouseEvent, id: string) => {
    e.stopPropagation();
    try {
      await fetch(`/api/conversations/${encodeURIComponent(id)}`, { method: "DELETE" });
      loadConversations();
    } catch (err) {
      console.error(err);
    }
  };

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

        {/* Conversation Selector */}
        <div className="relative" ref={dropdownRef}>
          <button
            onClick={handleToggleDropdown}
            className="flex items-center gap-1.5 font-mono text-slate-300 hover:text-white bg-slate-900/80 hover:bg-slate-800/90 px-2 py-1 rounded border border-slate-800 text-[11px] transition max-w-[180px]"
            title="Switch or view saved conversations"
          >
            <MessageSquare className="w-3 h-3 text-sky-400 shrink-0" />
            <span className="truncate">{conversationName || conversationId || "session"}</span>
            <ChevronDown className="w-3 h-3 text-slate-400 shrink-0" />
          </button>

          {showConvDropdown && (
            <div className="absolute left-0 mt-1.5 w-72 bg-[#0d1424] border border-slate-700/80 rounded-lg shadow-2xl py-1 z-50 text-slate-200">
              <div className="px-3 py-1.5 border-b border-slate-800 flex items-center justify-between text-[11px] text-slate-400">
                <span className="font-semibold uppercase tracking-wider">Conversations</span>
                <button
                  onClick={() => {
                    setShowConvDropdown(false);
                    onReset();
                  }}
                  className="flex items-center gap-1 text-sky-400 hover:text-sky-300 transition"
                  title="New conversation"
                >
                  <Plus className="w-3 h-3" />
                  <span>New</span>
                </button>
              </div>

              <div className="max-h-64 overflow-y-auto py-1">
                {conversations.length === 0 ? (
                  <div className="px-3 py-3 text-center text-slate-500 text-[11px]">
                    No saved conversations found
                  </div>
                ) : (
                  conversations.map((c) => {
                    const isActive = c.id === conversationId;
                    return (
                      <div
                        key={c.id}
                        onClick={() => {
                          setShowConvDropdown(false);
                          if (!isActive && onSelectConversation) {
                            onSelectConversation(c.id);
                          }
                        }}
                        className={`px-3 py-1.5 flex items-center justify-between cursor-pointer group transition text-[11px] ${
                          isActive
                            ? "bg-sky-950/40 text-sky-300 font-semibold"
                            : "hover:bg-slate-800/60 text-slate-300"
                        }`}
                      >
                        <div className="flex flex-col min-w-0 pr-2">
                          <span className="truncate font-sans font-medium text-slate-200">
                            {c.name || c.title || c.id}
                          </span>
                          <div className="flex items-center gap-2 text-[10px] text-slate-500 font-mono">
                            <span>{c.id}</span>
                            {c.updated_at && <span>{c.updated_at}</span>}
                          </div>
                        </div>

                        <div className="flex items-center gap-1 shrink-0">
                          {isActive && (
                            <span className="w-1.5 h-1.5 rounded-full bg-emerald-400" />
                          )}
                          <button
                            onClick={(e) => handleDeleteConversation(e, c.id)}
                            className="p-1 rounded opacity-0 group-hover:opacity-100 hover:text-rose-400 text-slate-500 transition"
                            title="Delete conversation"
                          >
                            <Trash2 className="w-3 h-3" />
                          </button>
                        </div>
                      </div>
                    );
                  })
                )}
              </div>
            </div>
          )}
        </div>

        <div
          className={`w-2 h-2 rounded-full ${
            connected ? "bg-emerald-500 shadow-[0_0_8px_rgba(16,185,129,0.6)]" : "bg-rose-500"
          }`}
          title={connected ? "WebSocket Connected" : "Connecting..."}
        />
      </div>

      {/* Center: Autonomous Agent Indicator */}
      <div className="hidden md:flex items-center gap-1.5 bg-slate-900/60 px-3 py-1 rounded-full border border-slate-800/80 text-[11px] font-mono text-slate-400">
        <Activity className="w-3 h-3 text-sky-400" />
        <span>LibHippo Autonomous Agent</span>
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

        {/* Reasoning Effort Selector */}
        <div
          className="flex items-center gap-1 bg-slate-900 border border-slate-800 rounded px-1.5 py-0.5"
          title="Main harness reasoning effort (KV cache preserved)"
        >
          <Brain className="w-3 h-3 text-purple-400" />
          <select
            value={reasoningEffort || "medium"}
            onChange={(e) => onReasoningEffortChange?.(e.target.value)}
            className="bg-transparent text-slate-200 text-xs font-mono outline-none cursor-pointer"
          >
            <option value="low">effort: low</option>
            <option value="medium">effort: med</option>
            <option value="high">effort: high</option>
          </select>
        </div>

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
