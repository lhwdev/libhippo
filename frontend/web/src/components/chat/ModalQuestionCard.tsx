import React, { useState } from "react";
import { HelpCircle, Send } from "lucide-react";
import { ModalQuestionEvent } from "../../types/events";

interface ModalQuestionCardProps {
  questionEvent: ModalQuestionEvent;
  onSubmit: (questionId: string, answers: any[]) => void;
}

export const ModalQuestionCard: React.FC<ModalQuestionCardProps> = ({ questionEvent, onSubmit }) => {
  const [selectedAnswers, setSelectedAnswers] = useState<Record<number, string | string[]>>({});
  const [customInputs, setCustomInputs] = useState<Record<number, string>>({});

  const handleOptionToggle = (qIdx: number, opt: string, isMulti?: boolean) => {
    if (isMulti) {
      const current = (selectedAnswers[qIdx] as string[]) || [];
      const updated = current.includes(opt)
        ? current.filter((x) => x !== opt)
        : [...current, opt];
      setSelectedAnswers({ ...selectedAnswers, [qIdx]: updated });
    } else {
      setSelectedAnswers({ ...selectedAnswers, [qIdx]: opt });
    }
  };

  const handleCustomChange = (qIdx: number, val: string) => {
    setCustomInputs({ ...customInputs, [qIdx]: val });
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const finalAnswers = questionEvent.questions.map((q, idx) => {
      const custom = customInputs[idx]?.trim();
      const sel = selectedAnswers[idx];
      if (custom) {
        return sel ? `${Array.isArray(sel) ? sel.join(", ") : sel} (Custom: ${custom})` : custom;
      }
      return sel || q.options[0] || "No response";
    });
    onSubmit(questionEvent.question_id, finalAnswers);
  };

  return (
    <div className="my-3 p-4 rounded-lg border-2 border-sky-500/40 bg-sky-950/20 backdrop-blur-sm text-xs">
      <div className="flex items-start gap-3">
        <div className="p-2 rounded-full bg-sky-500/20 text-sky-400">
          <HelpCircle className="w-5 h-5" />
        </div>
        <form onSubmit={handleSubmit} className="flex-1 space-y-4">
          <div className="flex items-center justify-between">
            <span className="font-semibold text-sky-300 text-sm">
              Clarification Needed (ask_question)
            </span>
            <span className="font-mono text-slate-500 text-[11px]">{questionEvent.question_id}</span>
          </div>

          {questionEvent.questions.map((q, qIdx) => {
            const currentSel = selectedAnswers[qIdx];

            return (
              <div key={qIdx} className="space-y-2 p-3 bg-slate-900/60 rounded border border-slate-800">
                <p className="font-medium text-slate-200">{q.question}</p>
                <div className="space-y-1.5 pl-1">
                  {q.options.map((opt, oIdx) => {
                    const isChecked = q.is_multi_select
                      ? Array.isArray(currentSel) && currentSel.includes(opt)
                      : currentSel === opt;

                    return (
                      <label
                        key={oIdx}
                        className={`flex items-center gap-2 p-2 rounded cursor-pointer transition ${
                          isChecked ? "bg-sky-500/20 border border-sky-500/30 text-white" : "hover:bg-slate-800/60 text-slate-300"
                        }`}
                      >
                        <input
                          type={q.is_multi_select ? "checkbox" : "radio"}
                          name={`question-${qIdx}`}
                          checked={isChecked}
                          onChange={() => handleOptionToggle(qIdx, opt, q.is_multi_select)}
                          className="text-sky-500 focus:ring-sky-500"
                        />
                        <span className="text-[12px]">{opt}</span>
                      </label>
                    );
                  })}
                </div>

                {/* Custom write-in */}
                <div className="pt-1">
                  <input
                    type="text"
                    placeholder="Or type a custom write-in response..."
                    value={customInputs[qIdx] || ""}
                    onChange={(e) => handleCustomChange(qIdx, e.target.value)}
                    className="w-full px-2.5 py-1.5 bg-slate-950 border border-slate-800 rounded text-slate-200 placeholder:text-slate-600 focus:outline-none focus:border-sky-500 text-xs"
                  />
                </div>
              </div>
            );
          })}

          <button
            type="submit"
            className="flex items-center gap-1.5 px-4 py-2 rounded-md bg-sky-600 hover:bg-sky-500 text-white font-medium transition shadow-sm"
          >
            <Send className="w-3.5 h-3.5" />
            <span>Submit Clarification</span>
          </button>
        </form>
      </div>
    </div>
  );
};
