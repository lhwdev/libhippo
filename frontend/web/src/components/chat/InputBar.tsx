import React, { useState, useRef, KeyboardEvent } from "react";
import { Send, Square, Sparkles, Compass, FileText, Terminal } from "lucide-react";
import { useAutocomplete } from "../../hooks/useAutocomplete";
import { CommandItem, FileItem } from "../../types/api";

interface InputBarProps {
  isStreaming: boolean;
  onSend: (text: string) => void;
  onContinue?: (guidance?: string) => void;
  onInterrupt: () => void;
  onSteer: (guidance: string) => void;
  onOpenSidecar: () => void;
}

export const InputBar: React.FC<InputBarProps> = ({
  isStreaming,
  onSend,
  onContinue,
  onInterrupt,
  onSteer,
  onOpenSidecar,
}) => {
  const [text, setText] = useState("");
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const autocomplete = useAutocomplete();

  const handleTextChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    const val = e.target.value;
    setText(val);
    const pos = e.target.selectionStart || val.length;
    autocomplete.handleInputChange(val, pos);
  };

  const handleApplySuggestion = (inserted: string) => {
    if (!textareaRef.current) return;
    const pos = textareaRef.current.selectionStart || text.length;
    const textBefore = text.slice(0, pos);
    const textAfter = text.slice(pos);

    // Replace the active token
    const lastWordMatch = textBefore.match(/[/@][^\s]*$/);
    if (lastWordMatch && lastWordMatch.index !== undefined) {
      const matchIndex = lastWordMatch.index;
      const newText = textBefore.slice(0, matchIndex) + inserted + textAfter;
      setText(newText);
      setTimeout(() => {
        if (textareaRef.current) {
          const newPos = matchIndex + inserted.length;
          textareaRef.current.setSelectionRange(newPos, newPos);
          textareaRef.current.focus();
        }
      }, 0);
    }
  };

  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    const handled = autocomplete.handleKeyDown(e, handleApplySuggestion);
    if (handled) return;

    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSubmit();
    }
  };

  const handleSubmit = () => {
    const trimmed = text.trim();
    if (!trimmed) return;

    // Check for frontend slash commands
    if (trimmed.startsWith("/btw ")) {
      // Direct sidecar query
      const q = trimmed.slice(5).trim();
      onOpenSidecar();
      setText("");
      autocomplete.close();
      return;
    }

    if (trimmed === "/continue" || trimmed.startsWith("/continue ")) {
      const guidance = trimmed === "/continue" ? "" : trimmed.slice(10).trim();
      if (onContinue) {
        onContinue(guidance);
      } else {
        onSend(guidance || "Continue working on the previous task.");
      }
      setText("");
      autocomplete.close();
      return;
    }

    if (isStreaming) {
      onSteer(trimmed);
    } else {
      onSend(trimmed);
    }
    setText("");
    autocomplete.close();
  };

  return (
    <div className="relative border-t border-slate-800 bg-[#0c1222] p-3 text-xs">
      {/* Autocomplete Popover */}
      {autocomplete.isOpen && autocomplete.currentItems.length > 0 && (
        <div className="absolute bottom-full left-4 right-4 mb-2 max-h-64 bg-[#111827] border border-slate-700/80 rounded-lg shadow-2xl overflow-y-auto z-50">
          <div className="px-3 py-1.5 border-b border-slate-800 text-[10px] uppercase font-mono font-semibold text-slate-400 flex items-center justify-between">
            <span>
              {autocomplete.activeTrigger === "command" ? "Slash Commands & Skills" : "Workspace Files"}
            </span>
            <span className="text-slate-500">↑↓ to navigate, Tab/Enter to complete</span>
          </div>

          <div className="p-1 space-y-0.5">
            {autocomplete.currentItems.map((item, idx) => {
              const isSelected = idx === autocomplete.selectedIndex;
              const isCmd = autocomplete.activeTrigger === "command";
              const cmd = item as CommandItem;
              const file = item as FileItem;

              return (
                <div
                  key={idx}
                  onClick={() =>
                    handleApplySuggestion(isCmd ? `${cmd.command} ` : `${file.path} `)
                  }
                  className={`flex items-center justify-between p-2 rounded cursor-pointer transition ${
                    isSelected ? "bg-sky-500/20 text-white border border-sky-500/30" : "hover:bg-slate-800/60 text-slate-300"
                  }`}
                >
                  <div className="flex items-center gap-2">
                    {isCmd ? (
                      cmd.type === "skill" ? (
                        <Sparkles className="w-3.5 h-3.5 text-purple-400" />
                      ) : (
                        <Terminal className="w-3.5 h-3.5 text-sky-400" />
                      )
                    ) : (
                      <FileText className="w-3.5 h-3.5 text-amber-400" />
                    )}
                    <span className="font-mono font-medium text-xs">
                      {isCmd ? cmd.command : file.path}
                    </span>
                  </div>
                  <span className="text-[11px] text-slate-500 truncate max-w-[260px]">
                    {isCmd ? cmd.description : file.name}
                  </span>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Primary Input Container */}
      <div className="flex items-end gap-2 bg-slate-950 border border-slate-800 focus-within:border-sky-500/60 rounded-lg p-2 transition">
        <textarea
          ref={textareaRef}
          rows={2}
          value={text}
          onChange={handleTextChange}
          onKeyDown={handleKeyDown}
          placeholder={
            isStreaming
              ? "Agent is running... Type to interrupt execution."
              : "Ask LibHippo anything... (Type '/' for commands/skills, '@' to mention files)"
          }
          className="flex-1 bg-transparent text-slate-100 placeholder:text-slate-500 text-xs font-sans outline-none resize-none leading-relaxed"
        />

        <div className="flex items-center gap-1.5 pb-0.5">
          <button
            onClick={onOpenSidecar}
            className="p-1.5 rounded hover:bg-slate-800 text-slate-400 hover:text-sky-300 transition"
            title="Ask Sidecar (/btw) against warm KV cache"
          >
            <Compass className="w-4 h-4" />
          </button>

          {isStreaming && (
            <button
              onClick={onInterrupt}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded bg-rose-600 hover:bg-rose-500 text-white font-medium transition shadow-sm animate-pulse"
              title="Interrupt and stop execution"
            >
              <Square className="w-3.5 h-3.5 fill-current" />
              <span>Stop</span>
            </button>
          )}

          <button
            onClick={handleSubmit}
            disabled={!text.trim()}
            className={`flex items-center gap-1.5 px-3 py-1.5 rounded font-medium transition shadow-sm ${
              isStreaming
                ? "bg-amber-600 hover:bg-amber-500 disabled:opacity-40 text-white"
                : "bg-sky-600 hover:bg-sky-500 disabled:opacity-40 disabled:hover:bg-sky-600 text-white"
            }`}
            title={isStreaming ? "Send message to steer running agent" : "Submit prompt (Enter)"}
          >
            <Send className="w-3.5 h-3.5" />
            <span>{isStreaming ? "Steer" : "Send"}</span>
          </button>
        </div>
      </div>
    </div>
  );
};
