import React, { useState } from "react";
import { Brain, ChevronDown, ChevronRight } from "lucide-react";
import { MarkdownRenderer } from "../common/MarkdownRenderer";

interface ReasoningCardProps {
  content: string;
  timestamp?: string;
  isStreaming?: boolean;
}

export const ReasoningCard: React.FC<ReasoningCardProps> = ({
  content,
  timestamp,
  isStreaming = false,
}) => {
  const [expanded, setExpanded] = useState(true);

  if (!content) return null;

  return (
    <div className="my-1.5 rounded border border-purple-900/40 bg-purple-950/10 overflow-hidden text-xs transition-all">
      <div
        onClick={() => setExpanded(!expanded)}
        className="flex items-center justify-between px-3 py-2 cursor-pointer hover:bg-purple-950/20 select-none transition"
      >
        <div className="flex items-center gap-2 overflow-hidden mr-2">
          {expanded ? (
            <ChevronDown className="w-3.5 h-3.5 text-purple-400 shrink-0" />
          ) : (
            <ChevronRight className="w-3.5 h-3.5 text-purple-400 shrink-0" />
          )}
          <Brain className={`w-3.5 h-3.5 text-purple-400 shrink-0 ${isStreaming ? "animate-pulse" : ""}`} />
          <span className="font-semibold text-purple-300 font-mono shrink-0">
            Thought Process
          </span>
          {!expanded && (
            <span className="text-slate-400 font-sans text-[11px] truncate">
              {content.split("\n")[0]}
            </span>
          )}
        </div>

        {timestamp && (
          <span className="text-[10px] text-slate-500 font-mono shrink-0">
            {timestamp}
          </span>
        )}
      </div>

      {expanded && (
        <div className="px-3 py-2.5 border-t border-purple-900/30 bg-[#090d18] text-slate-300 font-sans text-xs leading-relaxed select-text">
          <MarkdownRenderer content={content} />
        </div>
      )}
    </div>
  );
};
