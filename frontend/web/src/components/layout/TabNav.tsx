import React from "react";
import {
  MessageSquare,
  Compass,
  Sliders,
  Database,
  Terminal,
  FileCode,
} from "lucide-react";

export type NavTab = "chat" | "sidecar" | "settings" | "knowledge" | "tasks" | "artifacts";

interface TabNavProps {
  activeTab: NavTab;
  onTabChange: (tab: NavTab) => void;
  sidecarBadgeCount?: number;
}

export const TabNav: React.FC<TabNavProps> = ({
  activeTab,
  onTabChange,
  sidecarBadgeCount = 0,
}) => {
  const tabs: { id: NavTab; label: string; icon: React.ReactNode; badge?: number }[] = [
    { id: "chat", label: "Agent Console", icon: <MessageSquare className="w-4 h-4" /> },
    {
      id: "sidecar",
      label: "Sidecar (/btw)",
      icon: <Compass className="w-4 h-4" />,
      badge: sidecarBadgeCount,
    },
    { id: "settings", label: "Settings", icon: <Sliders className="w-4 h-4" /> },
    { id: "knowledge", label: "Knowledge", icon: <Database className="w-4 h-4" /> },
    { id: "tasks", label: "Tasks & Subs", icon: <Terminal className="w-4 h-4" /> },
    { id: "artifacts", label: "Artifacts", icon: <FileCode className="w-4 h-4" /> },
  ];

  return (
    <nav className="h-10 border-b border-slate-800 bg-[#090e1a] px-4 flex items-center gap-1 select-none">
      {tabs.map((tab) => {
        const isActive = activeTab === tab.id;
        return (
          <button
            key={tab.id}
            onClick={() => onTabChange(tab.id)}
            className={`flex items-center gap-2 px-3 py-1.5 rounded-t text-xs font-medium transition-all ${
              isActive
                ? "bg-[#111827] text-sky-400 border-b-2 border-sky-400 font-semibold"
                : "text-slate-400 hover:text-slate-200 hover:bg-slate-900/50"
            }`}
          >
            {tab.icon}
            <span>{tab.label}</span>
            {tab.badge ? (
              <span className="px-1.5 py-0.2 bg-sky-500/20 text-sky-300 text-[10px] rounded-full font-mono">
                {tab.badge}
              </span>
            ) : null}
          </button>
        );
      })}
    </nav>
  );
};
