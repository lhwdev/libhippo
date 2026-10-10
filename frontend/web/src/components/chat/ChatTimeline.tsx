import React, { useRef, useEffect } from "react";
import { Bot } from "lucide-react";
import { ChatMessage, ApprovalRequestEvent, ModalQuestionEvent } from "../../types/events";
import { MessageCard } from "./MessageCard";
import { ApprovalCard } from "./ApprovalCard";
import { ModalQuestionCard } from "./ModalQuestionCard";

interface ChatTimelineProps {
  messages: ChatMessage[];
  isStreaming: boolean;
  streamingResponse: string;
  pendingApproval: ApprovalRequestEvent | null;
  pendingQuestion: ModalQuestionEvent | null;
  onRespondApproval: (requestId: string, approved: boolean) => void;
  onSubmitQuestion: (questionId: string, answers: any[]) => void;
  onUndo?: (message: ChatMessage, promptText: string) => void;
}

export const ChatTimeline: React.FC<ChatTimelineProps> = ({
  messages,
  isStreaming,
  streamingResponse,
  pendingApproval,
  pendingQuestion,
  onRespondApproval,
  onSubmitQuestion,
  onUndo,
}) => {
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, streamingResponse, pendingApproval, pendingQuestion]);

  return (
    <div className="flex-1 overflow-y-auto p-4 space-y-2">
      {messages.length === 0 && !isStreaming && (
        <div className="h-full flex flex-col items-center justify-center text-center p-8 text-slate-500">
          <Bot className="w-12 h-12 text-slate-700 mb-3" />
          <h2 className="text-base font-semibold text-slate-300">LibHippo Autonomous Coding Agent</h2>
          <p className="text-xs text-slate-500 max-w-sm mt-1">
            Ready to plan, code, verify, and govern technical knowledge in this repository.
          </p>
          <div className="mt-4 flex flex-wrap gap-2 justify-center text-[11px] font-mono">
            <span className="px-2 py-1 bg-slate-900 rounded border border-slate-800 text-slate-400">
              /btw &lt;query&gt; (sidecar)
            </span>
            <span className="px-2 py-1 bg-slate-900 rounded border border-slate-800 text-slate-400">
              Type to interrupt agent
            </span>
            <span className="px-2 py-1 bg-slate-900 rounded border border-slate-800 text-slate-400">
              @&lt;file&gt; (mention)
            </span>
          </div>
        </div>
      )}

      {messages.map((m) => (
        <MessageCard key={m.id} message={m} onUndo={onUndo} />
      ))}

      {/* Live In-Flight Stream */}
      {isStreaming && (
        <div className="py-3 px-4 rounded-lg my-1.5 bg-[#0d1424] border border-sky-500/30 text-slate-200">
          <div className="flex items-center gap-2 mb-1.5 select-none text-emerald-400 font-semibold font-mono text-xs">
            <Bot className="w-3.5 h-3.5 animate-pulse" />
            <span>Assistant (Thinking & Coding...)</span>
          </div>
          <div className="whitespace-pre-wrap leading-relaxed font-sans text-sm selection:bg-sky-500/30 select-text">
            {streamingResponse || <span className="text-slate-500 italic">Formulating plan...</span>}
          </div>
        </div>
      )}

      {/* Interactive Gates */}
      {pendingApproval && (
        <ApprovalCard request={pendingApproval} onRespond={onRespondApproval} />
      )}

      {pendingQuestion && (
        <ModalQuestionCard questionEvent={pendingQuestion} onSubmit={onSubmitQuestion} />
      )}

      <div ref={bottomRef} />
    </div>
  );
};
