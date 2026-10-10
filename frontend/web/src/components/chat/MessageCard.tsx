import React from "react";
import { User, Bot, AlertOctagon, Terminal, Info, BookOpen, RotateCcw } from "lucide-react";
import { ChatMessage } from "../../types/events";
import { ToolCallCard } from "./ToolCallCard";
import { KnowledgeAgentCard } from "./KnowledgeAgentCard";
import { MarkdownRenderer } from "../common/MarkdownRenderer";

interface MessageCardProps {
  message: ChatMessage;
  onUndo?: (message: ChatMessage, promptText: string) => void;
}

export const MessageCard: React.FC<MessageCardProps> = ({ message, onUndo }) => {
  const isUser = message.role === "user";
  const isAssistant = message.role === "assistant";
  const isInterrupt = message.role === "interrupt";
  const isSystem = message.role === "system";

  let displayContent = message.content;
  if (isUser) {
    const promptMatch = displayContent.match(/<USER_PROMPT[^>]*>([\s\S]*?)(?:<\/USER_PROMPT>|$)/i);
    if (promptMatch) {
      displayContent = promptMatch[1].trim();
    }
  }

  return (
    <div
      className={`py-3 px-4 rounded-lg my-1.5 transition-all text-xs ${
        isUser
          ? "bg-[#131c31] border border-sky-900/40 text-slate-100"
          : isInterrupt
          ? "bg-rose-950/20 border border-rose-900/40 text-rose-200"
          : isSystem
          ? "bg-slate-900/40 border border-slate-800 text-slate-400"
          : "bg-[#0d1424] border border-slate-800/80 text-slate-200"
      }`}
    >
      <div className="flex items-center justify-between gap-2 mb-1.5 select-none">
        <div className="flex items-center gap-2">
          {isUser ? (
            <div className="flex items-center gap-1.5 text-sky-400 font-semibold font-mono">
              <User className="w-3.5 h-3.5" />
              <span>User</span>
            </div>
          ) : isAssistant ? (
            <div className="flex items-center gap-1.5 text-emerald-400 font-semibold font-mono">
              <Bot className="w-3.5 h-3.5" />
              <span>Assistant</span>
            </div>
          ) : isInterrupt ? (
            <div className="flex items-center gap-1.5 text-rose-400 font-semibold font-mono">
              <AlertOctagon className="w-3.5 h-3.5" />
              <span>Interrupt</span>
            </div>
          ) : (
            <div className="flex items-center gap-1.5 text-slate-400 font-semibold font-mono">
              <Info className="w-3.5 h-3.5" />
              <span>System</span>
            </div>
          )}
        </div>
        <div className="flex items-center gap-2">
          {message.timestamp && (
            <span className="text-[10px] text-slate-500 font-mono">{message.timestamp}</span>
          )}
          {isUser && onUndo && (
            <button
              onClick={() => onUndo(message, displayContent)}
              className="flex items-center gap-1 text-[10px] text-slate-400 hover:text-sky-300 bg-sky-950/40 hover:bg-sky-900/60 border border-sky-800/40 px-1.5 py-0.5 rounded transition cursor-pointer"
              title="Undo this message and restore to input box"
            >
              <RotateCcw className="w-3 h-3 text-sky-400" />
              <span>Undo</span>
            </button>
          )}
        </div>
      </div>

      <div className="leading-relaxed font-sans text-sm selection:bg-sky-500/30 select-text">
        <MarkdownRenderer content={displayContent} />
      </div>

      {message.toolCalls && message.toolCalls.length > 0 && (
        <div className="mt-3 pt-2 border-t border-slate-800/80">
          <div className="flex items-center gap-1.5 text-[11px] text-slate-400 font-mono mb-1">
            <Terminal className="w-3 h-3 text-sky-400" />
            <span>Tool Executions ({message.toolCalls.length}):</span>
          </div>
          {message.toolCalls.map((tc) => (
            <ToolCallCard key={tc.id} toolCall={tc} />
          ))}
        </div>
      )}

      {message.knowledgeEvents && message.knowledgeEvents.length > 0 && (
        <div className="mt-3 pt-2 border-t border-slate-800/80">
          <div className="flex items-center gap-1.5 text-[11px] text-purple-400 font-mono mb-1">
            <BookOpen className="w-3 h-3 text-purple-400" />
            <span>Knowledge Agents ({message.knowledgeEvents.length}):</span>
          </div>
          {message.knowledgeEvents.map((ke, idx) => (
            <KnowledgeAgentCard key={`ke-${idx}-${ke.agent}-${ke.action}`} event={ke} />
          ))}
        </div>
      )}
    </div>
  );
};
