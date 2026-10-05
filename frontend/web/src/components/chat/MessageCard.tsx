import React from "react";
import { User, Bot, AlertOctagon, Terminal, Info, BookOpen } from "lucide-react";
import { ChatMessage } from "../../types/events";
import { ToolCallCard } from "./ToolCallCard";
import { KnowledgeAgentCard } from "./KnowledgeAgentCard";

interface MessageCardProps {
  message: ChatMessage;
}

export const MessageCard: React.FC<MessageCardProps> = ({ message }) => {
  const isUser = message.role === "user";
  const isAssistant = message.role === "assistant";
  const isInterrupt = message.role === "interrupt";
  const isSystem = message.role === "system";

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
        {message.timestamp && (
          <span className="text-[10px] text-slate-500 font-mono">{message.timestamp}</span>
        )}
      </div>

      <div className="whitespace-pre-wrap leading-relaxed font-sans text-sm selection:bg-sky-500/30 select-text">
        {message.content}
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
