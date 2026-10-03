import React, { useState, useEffect } from "react";
import { Terminal, Users, Play, Square, Send, RefreshCw, FileText } from "lucide-react";
import { TaskItem, SubagentItem } from "../../types/api";

export const TaskMonitor: React.FC = () => {
  const [tasks, setTasks] = useState<TaskItem[]>([]);
  const [subagents, setSubagents] = useState<SubagentItem[]>([]);
  const [selectedTask, setSelectedTask] = useState<string | null>(null);
  const [taskLog, setTaskLog] = useState<string>("");
  const [stdinInput, setStdinInput] = useState("");
  const [selectedSubagent, setSelectedSubagent] = useState<string | null>(null);
  const [subagentMessage, setSubagentMessage] = useState("");

  const refreshTasks = () => {
    fetch("/api/tasks")
      .then((res) => res.json())
      .then((data) => setTasks(data.tasks || []))
      .catch((err) => console.error(err));

    fetch("/api/subagents")
      .then((res) => res.json())
      .then((data) => setSubagents(data.subagents || []))
      .catch((err) => console.error(err));
  };

  useEffect(() => {
    refreshTasks();
    const timer = setInterval(refreshTasks, 3000);
    return () => clearInterval(timer);
  }, []);

  const handleSelectTask = (tid: string) => {
    setSelectedTask(tid);
    fetch(`/api/tasks/${tid}/log`)
      .then((res) => res.json())
      .then((data) => setTaskLog(data.log || "(No log output)"))
      .catch(() => setTaskLog("(Log file not available)"));
  };

  const handleKillTask = async (tid: string) => {
    await fetch(`/api/tasks/${tid}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action: "kill" }),
    });
    refreshTasks();
  };

  const handleSendStdin = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!selectedTask || !stdinInput) return;
    await fetch(`/api/tasks/${selectedTask}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action: "send_input", input: stdinInput }),
    });
    setStdinInput("");
    handleSelectTask(selectedTask);
  };

  const handleSendMessageToSubagent = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!selectedSubagent || !subagentMessage.trim()) return;
    await fetch(`/api/subagents/${selectedSubagent}/message`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: subagentMessage }),
    });
    setSubagentMessage("");
    refreshTasks();
  };

  return (
    <div className="flex-1 flex flex-col md:flex-row bg-[#090e1a] overflow-hidden text-xs">
      {/* Left Column: Tasks and Subagents List */}
      <div className="w-full md:w-96 border-r border-slate-800 flex flex-col bg-[#0c1222]">
        <div className="p-3 border-b border-slate-800 flex items-center justify-between">
          <div className="flex items-center gap-2 font-semibold text-slate-200">
            <Terminal className="w-4 h-4 text-sky-400" />
            <span>Sandbox Tasks & Subagents</span>
          </div>
          <button onClick={refreshTasks} className="p-1 hover:bg-slate-800 rounded text-slate-400">
            <RefreshCw className="w-3.5 h-3.5" />
          </button>
        </div>

        {/* Tasks List */}
        <div className="flex-1 overflow-y-auto p-2 space-y-2">
          <span className="text-[10px] font-mono uppercase font-semibold text-slate-500 px-1">
            Background Tasks ({tasks.length})
          </span>
          {tasks.length === 0 ? (
            <div className="p-3 text-slate-500 text-center">No active background tasks</div>
          ) : (
            tasks.map((t) => (
              <div
                key={t.task_id}
                onClick={() => handleSelectTask(t.task_id)}
                className={`p-2.5 rounded border cursor-pointer transition space-y-1 ${
                  selectedTask === t.task_id
                    ? "bg-sky-500/20 border-sky-500/40 text-white"
                    : "bg-[#090e1a] border-slate-800/80 hover:bg-slate-900/60 text-slate-300"
                }`}
              >
                <div className="flex items-center justify-between font-mono text-[11px]">
                  <span className="font-semibold text-sky-300">{t.task_id}</span>
                  <span
                    className={`px-1.5 py-0.2 rounded text-[10px] uppercase font-semibold ${
                      t.status === "running" ? "bg-emerald-500/20 text-emerald-400" : "bg-slate-800 text-slate-400"
                    }`}
                  >
                    {t.status}
                  </span>
                </div>
                <div className="font-mono text-[11px] truncate text-slate-400">{t.command}</div>
              </div>
            ))
          )}

          {/* Subagents List */}
          <div className="pt-3">
            <span className="text-[10px] font-mono uppercase font-semibold text-slate-500 px-1 flex items-center gap-1">
              <Users className="w-3 h-3 text-purple-400" />
              <span>Spawned Subagents ({subagents.length})</span>
            </span>
            <div className="mt-1 space-y-1.5">
              {subagents.length === 0 ? (
                <div className="p-3 text-slate-500 text-center">No active subagents</div>
              ) : (
                subagents.map((s) => (
                  <div
                    key={s.subagent_id}
                    onClick={() => setSelectedSubagent(s.subagent_id)}
                    className={`p-2 rounded border cursor-pointer transition space-y-1 ${
                      selectedSubagent === s.subagent_id
                        ? "bg-purple-500/20 border-purple-500/40 text-white"
                        : "bg-[#090e1a] border-slate-800/80 hover:bg-slate-900/60 text-slate-300"
                    }`}
                  >
                    <div className="flex items-center justify-between font-mono text-[11px]">
                      <span className="font-semibold text-purple-300">{s.role}</span>
                      <span className="text-[10px] text-slate-500">{s.state}</span>
                    </div>
                    <div className="text-[11px] text-slate-400 line-clamp-1">{s.prompt}</div>
                  </div>
                ))
              )}
            </div>
          </div>
        </div>
      </div>

      {/* Right Column: Active Task Log & Stdin Terminal */}
      <div className="flex-1 flex flex-col bg-[#080d19] overflow-hidden">
        {selectedTask ? (
          <>
            <div className="p-3 border-b border-slate-800 bg-[#0c1222] flex items-center justify-between">
              <div className="flex items-center gap-2 font-mono">
                <FileText className="w-4 h-4 text-sky-400" />
                <span className="font-semibold text-slate-200">Task Log: {selectedTask}</span>
              </div>
              <button
                onClick={() => handleKillTask(selectedTask)}
                className="flex items-center gap-1 px-2.5 py-1 bg-rose-600/80 hover:bg-rose-600 text-white rounded text-[11px] font-medium transition"
              >
                <Square className="w-3 h-3 fill-current" />
                <span>Kill Task</span>
              </button>
            </div>

            <pre className="flex-1 p-4 font-mono text-[11px] text-slate-300 bg-slate-950 overflow-y-auto whitespace-pre-wrap selection:bg-sky-500/30">
              {taskLog}
            </pre>

            {/* Stdin Bar */}
            <form onSubmit={handleSendStdin} className="p-2.5 border-t border-slate-800 bg-[#0c1222] flex items-center gap-2">
              <span className="text-slate-500 font-mono text-xs pl-2">&gt;</span>
              <input
                type="text"
                placeholder="Send input (stdin) to running process..."
                value={stdinInput}
                onChange={(e) => setStdinInput(e.target.value)}
                className="flex-1 bg-slate-950 border border-slate-800 rounded px-3 py-1.5 text-slate-100 font-mono text-xs outline-none"
              />
              <button
                type="submit"
                className="px-3 py-1.5 rounded bg-sky-600 hover:bg-sky-500 text-white font-medium text-xs transition"
              >
                Send
              </button>
            </form>
          </>
        ) : selectedSubagent ? (
          <div className="flex-1 flex flex-col p-4 space-y-4">
            <h3 className="text-sm font-semibold text-slate-200 pb-2 border-b border-slate-800 font-mono">
              Subagent: {selectedSubagent}
            </h3>
            <form onSubmit={handleSendMessageToSubagent} className="space-y-2">
              <textarea
                rows={3}
                placeholder="Send message or instructions to subagent..."
                value={subagentMessage}
                onChange={(e) => setSubagentMessage(e.target.value)}
                className="w-full p-2.5 bg-slate-950 border border-slate-800 rounded text-slate-200 text-xs outline-none"
              />
              <button
                type="submit"
                className="flex items-center gap-1.5 px-3 py-1.5 bg-purple-600 hover:bg-purple-500 text-white rounded text-xs font-medium transition"
              >
                <Send className="w-3.5 h-3.5" />
                <span>Send Message</span>
              </button>
            </form>
          </div>
        ) : (
          <div className="flex-1 flex flex-col items-center justify-center text-slate-500">
            <Terminal className="w-8 h-8 text-slate-700 mb-2" />
            <p>Select a running task or subagent to view live logs and send input.</p>
          </div>
        )}
      </div>
    </div>
  );
};
