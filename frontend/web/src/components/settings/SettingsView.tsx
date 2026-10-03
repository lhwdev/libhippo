import React, { useState, useEffect } from "react";
import {
  Shield,
  FileCode,
  Cpu,
  Sparkles,
  Save,
  Plus,
  Trash2,
  CheckCircle,
  Network,
} from "lucide-react";
import { ProjectSecurityPolicy, GlobalMcpConfig, SkillDefinition, RuntimeConfig } from "../../types/settings";

export const SettingsView: React.FC = () => {
  const [activeSubTab, setActiveSubTab] = useState<"security" | "agents" | "mcp" | "skills" | "runtime">("security");
  const [statusMessage, setStatusMessage] = useState<string | null>(null);

  // 1. Security Policy State
  const [policy, setPolicy] = useState<ProjectSecurityPolicy | null>(null);
  const [newRule, setNewRule] = useState<{ category: "read_file" | "write_file" | "command"; type: "allow" | "deny" | "ask"; text: string }>({
    category: "command",
    type: "allow",
    text: "",
  });
  const [newDomain, setNewDomain] = useState("");

  // 2. AGENTS.md State
  const [projectAgents, setProjectAgents] = useState("");
  const [globalAgents, setGlobalAgents] = useState("");
  const [agentsTab, setAgentsTab] = useState<"project" | "global">("project");

  // 3. MCP State
  const [mcp, setMcp] = useState<GlobalMcpConfig>({ mcpServers: {} });
  const [newServerName, setNewServerName] = useState("");
  const [newServerCmd, setNewServerCmd] = useState("");
  const [newServerArgs, setNewServerArgs] = useState("");

  // 4. Skills State
  const [skills, setSkills] = useState<SkillDefinition[]>([]);

  // 5. Runtime Config State
  const [runtimeConfig, setRuntimeConfig] = useState<RuntimeConfig | null>(null);

  useEffect(() => {
    // Load project settings
    fetch("/api/settings/project")
      .then((res) => res.json())
      .then((data) => {
        if (data.security_policy) setPolicy(data.security_policy);
        if (data.agents_markdown) setProjectAgents(data.agents_markdown);
        if (data.skills) setSkills(data.skills);
      });

    // Load global settings
    fetch("/api/settings/global")
      .then((res) => res.json())
      .then((data) => {
        if (data.agents_markdown) setGlobalAgents(data.agents_markdown);
        if (data.mcp) setMcp(data.mcp);
      });

    // Load runtime config
    fetch("/api/settings/runtime")
      .then((res) => res.json())
      .then((data) => setRuntimeConfig(data));
  }, []);

  const flashStatus = (msg: string) => {
    setStatusMessage(msg);
    setTimeout(() => setStatusMessage(null), 3000);
  };

  // Save Security Policy
  const handleSavePolicy = async () => {
    if (!policy) return;
    const res = await fetch("/api/settings/project", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ security_policy: policy, agents_markdown: projectAgents }),
    });
    if (res.ok) flashStatus("Project security policy saved successfully.");
  };

  // Add rule to policy
  const handleAddRule = () => {
    if (!policy || !newRule.text.trim()) return;
    const cat = policy[newRule.category];
    const updated = {
      ...policy,
      [newRule.category]: {
        ...cat,
        [newRule.type]: [...cat[newRule.type], newRule.text.trim()],
      },
    };
    setPolicy(updated);
    setNewRule({ ...newRule, text: "" });
  };

  // Remove rule
  const handleRemoveRule = (category: "read_file" | "write_file" | "command", type: "allow" | "deny" | "ask", idx: number) => {
    if (!policy) return;
    const list = [...policy[category][type]];
    list.splice(idx, 1);
    setPolicy({
      ...policy,
      [category]: {
        ...policy[category],
        [type]: list,
      },
    });
  };

  // Save AGENTS.md
  const handleSaveAgents = async () => {
    if (agentsTab === "project") {
      await fetch("/api/settings/project", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ agents_markdown: projectAgents }),
      });
      flashStatus("Project AGENTS.md updated.");
    } else {
      await fetch("/api/settings/global", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ agents_markdown: globalAgents }),
      });
      flashStatus("Global AGENTS.md updated.");
    }
  };

  // Save MCP
  const handleSaveMcp = async () => {
    await fetch("/api/settings/global", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mcp }),
    });
    flashStatus("Global MCP configuration saved.");
  };

  const handleAddMcpServer = () => {
    if (!newServerName.trim() || !newServerCmd.trim()) return;
    const argsArray = newServerArgs.split(/\s+/).filter(Boolean);
    setMcp({
      mcpServers: {
        ...mcp.mcpServers,
        [newServerName.trim()]: {
          command: newServerCmd.trim(),
          args: argsArray,
          env: {},
        },
      },
    });
    setNewServerName("");
    setNewServerCmd("");
    setNewServerArgs("");
  };

  const handleDeleteMcpServer = (key: string) => {
    const updated = { ...mcp.mcpServers };
    delete updated[key];
    setMcp({ mcpServers: updated });
  };

  return (
    <div className="flex-1 flex flex-col bg-[#090e1a] overflow-hidden text-xs">
      {/* Subtab Navigation */}
      <div className="flex items-center justify-between px-4 py-2 border-b border-slate-800 bg-[#0d1424]">
        <div className="flex items-center gap-2">
          <button
            onClick={() => setActiveSubTab("security")}
            className={`flex items-center gap-1.5 px-3 py-1 rounded transition ${
              activeSubTab === "security" ? "bg-sky-500/20 text-sky-300 font-semibold" : "text-slate-400 hover:text-slate-200"
            }`}
          >
            <Shield className="w-3.5 h-3.5" />
            <span>Security Policy</span>
          </button>
          <button
            onClick={() => setActiveSubTab("agents")}
            className={`flex items-center gap-1.5 px-3 py-1 rounded transition ${
              activeSubTab === "agents" ? "bg-sky-500/20 text-sky-300 font-semibold" : "text-slate-400 hover:text-slate-200"
            }`}
          >
            <FileCode className="w-3.5 h-3.5" />
            <span>AGENTS.md Rules</span>
          </button>
          <button
            onClick={() => setActiveSubTab("mcp")}
            className={`flex items-center gap-1.5 px-3 py-1 rounded transition ${
              activeSubTab === "mcp" ? "bg-sky-500/20 text-sky-300 font-semibold" : "text-slate-400 hover:text-slate-200"
            }`}
          >
            <Cpu className="w-3.5 h-3.5" />
            <span>Global MCP</span>
          </button>
          <button
            onClick={() => setActiveSubTab("skills")}
            className={`flex items-center gap-1.5 px-3 py-1 rounded transition ${
              activeSubTab === "skills" ? "bg-sky-500/20 text-sky-300 font-semibold" : "text-slate-400 hover:text-slate-200"
            }`}
          >
            <Sparkles className="w-3.5 h-3.5" />
            <span>Skills Catalog</span>
          </button>
        </div>

        {statusMessage && (
          <div className="flex items-center gap-1.5 text-emerald-400 font-mono text-[11px] animate-fade-in">
            <CheckCircle className="w-3.5 h-3.5" />
            <span>{statusMessage}</span>
          </div>
        )}
      </div>

      {/* Main Content Area */}
      <div className="flex-1 overflow-y-auto p-4 max-w-5xl w-full mx-auto space-y-4">
        {/* TAB 1: SECURITY POLICY */}
        {activeSubTab === "security" && policy && (
          <div className="space-y-4">
            <div className="flex items-center justify-between pb-2 border-b border-slate-800">
              <div>
                <h3 className="text-sm font-semibold text-slate-200">Project Security Policy</h3>
                <p className="text-[11px] text-slate-500 font-mono">
                  Stored at ~/.config/libhippo/projects/{policy.project_id}.json
                </p>
              </div>
              <button
                onClick={handleSavePolicy}
                className="flex items-center gap-1.5 px-3 py-1.5 bg-sky-600 hover:bg-sky-500 text-white font-medium rounded transition"
              >
                <Save className="w-3.5 h-3.5" />
                <span>Save Policy</span>
              </button>
            </div>

            {/* Network Toggle */}
            <div className="p-3 bg-slate-900/60 rounded border border-slate-800 flex items-center justify-between">
              <div className="flex items-center gap-2">
                <Network className="w-4 h-4 text-sky-400" />
                <div>
                  <span className="font-semibold text-slate-200">Outbound Network Access</span>
                  <p className="text-[11px] text-slate-500">Unshares network namespace in Bubblewrap sandbox when disabled</p>
                </div>
              </div>
              <input
                type="checkbox"
                checked={policy.allow_network}
                onChange={(e) => setPolicy({ ...policy, allow_network: e.target.checked })}
                className="w-4 h-4 text-sky-500 rounded focus:ring-sky-500 cursor-pointer"
              />
            </div>

            {/* Rule Categories: command, read_file, write_file */}
            {(["command", "read_file", "write_file"] as const).map((catKey) => {
              const cat = policy[catKey];
              return (
                <div key={catKey} className="p-3 bg-slate-900/40 rounded border border-slate-800 space-y-3">
                  <span className="font-mono font-semibold text-slate-300 uppercase tracking-wider text-[11px]">
                    {catKey} Rules
                  </span>

                  {(["allow", "deny", "ask"] as const).map((typeKey) => {
                    const color =
                      typeKey === "allow"
                        ? "text-emerald-400 bg-emerald-500/10 border-emerald-500/30"
                        : typeKey === "deny"
                        ? "text-rose-400 bg-rose-500/10 border-rose-500/30"
                        : "text-amber-400 bg-amber-500/10 border-amber-500/30";

                    return (
                      <div key={typeKey} className="space-y-1">
                        <span className="text-[10px] font-mono uppercase text-slate-500">{typeKey} Patterns:</span>
                        <div className="flex flex-wrap gap-1.5">
                          {cat[typeKey].length === 0 && (
                            <span className="text-slate-600 italic text-[11px]">(None)</span>
                          )}
                          {cat[typeKey].map((rule, idx) => (
                            <span
                              key={idx}
                              className={`px-2 py-0.5 rounded border text-[11px] font-mono flex items-center gap-1.5 ${color}`}
                            >
                              <span>{rule}</span>
                              <button
                                onClick={() => handleRemoveRule(catKey, typeKey, idx)}
                                className="hover:opacity-75"
                              >
                                &times;
                              </button>
                            </span>
                          ))}
                        </div>
                      </div>
                    );
                  })}
                </div>
              );
            })}

            {/* Add New Rule */}
            <div className="p-3 bg-slate-900/80 rounded border border-slate-800 flex items-center gap-2">
              <select
                value={newRule.category}
                onChange={(e) => setNewRule({ ...newRule, category: e.target.value as any })}
                className="bg-slate-950 border border-slate-800 rounded px-2 py-1 text-slate-200 outline-none"
              >
                <option value="command">command</option>
                <option value="read_file">read_file</option>
                <option value="write_file">write_file</option>
              </select>

              <select
                value={newRule.type}
                onChange={(e) => setNewRule({ ...newRule, type: e.target.value as any })}
                className="bg-slate-950 border border-slate-800 rounded px-2 py-1 text-slate-200 outline-none"
              >
                <option value="allow">allow</option>
                <option value="deny">deny</option>
                <option value="ask">ask</option>
              </select>

              <input
                type="text"
                placeholder="Glob pattern (e.g. 'npm test*', 'src/**', '.git/**')..."
                value={newRule.text}
                onChange={(e) => setNewRule({ ...newRule, text: e.target.value })}
                className="flex-1 bg-slate-950 border border-slate-800 rounded px-3 py-1 text-slate-200 font-mono text-xs outline-none"
              />

              <button
                onClick={handleAddRule}
                disabled={!newRule.text.trim()}
                className="flex items-center gap-1 px-3 py-1 bg-slate-800 hover:bg-slate-700 disabled:opacity-40 text-slate-200 font-medium rounded transition"
              >
                <Plus className="w-3.5 h-3.5" />
                <span>Add</span>
              </button>
            </div>
          </div>
        )}

        {/* TAB 2: AGENTS.MD */}
        {activeSubTab === "agents" && (
          <div className="space-y-3">
            <div className="flex items-center justify-between pb-2 border-b border-slate-800">
              <div className="flex items-center gap-2">
                <button
                  onClick={() => setAgentsTab("project")}
                  className={`px-3 py-1 rounded transition ${
                    agentsTab === "project" ? "bg-sky-500/20 text-sky-300 font-semibold" : "text-slate-400 hover:text-slate-200"
                  }`}
                >
                  Project AGENTS.md
                </button>
                <button
                  onClick={() => setAgentsTab("global")}
                  className={`px-3 py-1 rounded transition ${
                    agentsTab === "global" ? "bg-sky-500/20 text-sky-300 font-semibold" : "text-slate-400 hover:text-slate-200"
                  }`}
                >
                  Global AGENTS.md (~/.config/libhippo/)
                </button>
              </div>

              <button
                onClick={handleSaveAgents}
                className="flex items-center gap-1.5 px-3 py-1.5 bg-sky-600 hover:bg-sky-500 text-white font-medium rounded transition"
              >
                <Save className="w-3.5 h-3.5" />
                <span>Save Rules</span>
              </button>
            </div>

            <textarea
              rows={22}
              value={agentsTab === "project" ? projectAgents : globalAgents}
              onChange={(e) =>
                agentsTab === "project" ? setProjectAgents(e.target.value) : setGlobalAgents(e.target.value)
              }
              placeholder="# Project or Global Guidelines..."
              className="w-full p-3 bg-slate-950 border border-slate-800 rounded font-mono text-xs text-slate-200 focus:outline-none focus:border-sky-500 leading-relaxed resize-y"
            />
          </div>
        )}

        {/* TAB 3: GLOBAL MCP */}
        {activeSubTab === "mcp" && (
          <div className="space-y-4">
            <div className="flex items-center justify-between pb-2 border-b border-slate-800">
              <div>
                <h3 className="text-sm font-semibold text-slate-200">Global MCP Servers</h3>
                <p className="text-[11px] text-slate-500 font-mono">Configured in ~/.config/libhippo/mcp.json</p>
              </div>
              <button
                onClick={handleSaveMcp}
                className="flex items-center gap-1.5 px-3 py-1.5 bg-sky-600 hover:bg-sky-500 text-white font-medium rounded transition"
              >
                <Save className="w-3.5 h-3.5" />
                <span>Save MCP Config</span>
              </button>
            </div>

            <div className="space-y-2">
              {Object.keys(mcp.mcpServers).length === 0 && (
                <div className="p-8 text-center text-slate-500 border border-slate-800 rounded">
                  No MCP servers currently configured.
                </div>
              )}
              {Object.entries(mcp.mcpServers).map(([name, srv]) => (
                <div
                  key={name}
                  className="p-3 bg-slate-900/60 rounded border border-slate-800 flex items-center justify-between"
                >
                  <div className="space-y-1">
                    <span className="font-semibold text-slate-200">{name}</span>
                    <div className="font-mono text-[11px] text-slate-400">
                      <code>
                        {srv.command} {srv.args.join(" ")}
                      </code>
                    </div>
                  </div>
                  <button
                    onClick={() => handleDeleteMcpServer(name)}
                    className="p-1.5 text-rose-400 hover:bg-rose-950/40 rounded transition"
                  >
                    <Trash2 className="w-4 h-4" />
                  </button>
                </div>
              ))}
            </div>

            {/* Add Server */}
            <div className="p-3 bg-slate-900/80 rounded border border-slate-800 space-y-2">
              <span className="font-semibold text-slate-300">Add MCP Server</span>
              <div className="grid grid-cols-1 md:grid-cols-3 gap-2">
                <input
                  type="text"
                  placeholder="Server identifier (e.g. 'github')..."
                  value={newServerName}
                  onChange={(e) => setNewServerName(e.target.value)}
                  className="bg-slate-950 border border-slate-800 rounded px-2.5 py-1.5 text-slate-200 text-xs outline-none"
                />
                <input
                  type="text"
                  placeholder="Command (e.g. 'npx', 'uvx')..."
                  value={newServerCmd}
                  onChange={(e) => setNewServerCmd(e.target.value)}
                  className="bg-slate-950 border border-slate-800 rounded px-2.5 py-1.5 text-slate-200 text-xs outline-none"
                />
                <input
                  type="text"
                  placeholder="Arguments (e.g. '-y @modelcontextprotocol/server-github')..."
                  value={newServerArgs}
                  onChange={(e) => setNewServerArgs(e.target.value)}
                  className="bg-slate-950 border border-slate-800 rounded px-2.5 py-1.5 text-slate-200 text-xs outline-none"
                />
              </div>
              <button
                onClick={handleAddMcpServer}
                disabled={!newServerName.trim() || !newServerCmd.trim()}
                className="flex items-center gap-1 px-3 py-1.5 bg-slate-800 hover:bg-slate-700 disabled:opacity-40 text-slate-200 rounded transition font-medium"
              >
                <Plus className="w-3.5 h-3.5" />
                <span>Add Server</span>
              </button>
            </div>
          </div>
        )}

        {/* TAB 4: SKILLS CATALOG */}
        {activeSubTab === "skills" && (
          <div className="space-y-3">
            <h3 className="text-sm font-semibold text-slate-200 pb-2 border-b border-slate-800">
              Discovered Skills (.agents/skills/*)
            </h3>
            {skills.length === 0 ? (
              <div className="p-8 text-center text-slate-500 border border-slate-800 rounded">
                No custom skills discovered in .agents/skills or ~/.agents/skills.
              </div>
            ) : (
              skills.map((skill) => (
                <div key={skill.name} className="p-3 bg-slate-900/60 rounded border border-slate-800 space-y-2">
                  <div className="flex items-center justify-between">
                    <span className="font-semibold text-purple-300 font-mono">/{skill.name}</span>
                    <span className="text-[11px] text-slate-500 font-mono">{skill.skill_path}</span>
                  </div>
                  <p className="text-slate-300">{skill.description}</p>
                  <pre className="p-2 bg-slate-950 rounded text-slate-400 font-mono text-[11px] whitespace-pre-wrap max-h-32 overflow-y-auto">
                    {skill.system_prompt}
                  </pre>
                </div>
              ))
            )}
          </div>
        )}
      </div>
    </div>
  );
};
