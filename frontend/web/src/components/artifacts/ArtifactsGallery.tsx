import React, { useState, useEffect } from "react";
import { FileCode, FileText, CheckCircle, RefreshCw } from "lucide-react";
import { ArtifactItem } from "../../types/api";

export const ArtifactsGallery: React.FC = () => {
  const [artifacts, setArtifacts] = useState<ArtifactItem[]>([]);
  const [selectedArtifact, setSelectedArtifact] = useState<string | null>(null);
  const [artifactContent, setArtifactContent] = useState<string>("");

  const refreshArtifacts = () => {
    fetch("/api/artifacts")
      .then((res) => res.json())
      .then((data) => {
        setArtifacts(data.artifacts || []);
        if (data.artifacts && data.artifacts.length > 0 && !selectedArtifact) {
          handleSelect(data.artifacts[0].name);
        }
      })
      .catch((err) => console.error(err));
  };

  useEffect(() => {
    refreshArtifacts();
  }, []);

  const handleSelect = (name: string) => {
    setSelectedArtifact(name);
    fetch(`/api/artifacts/${encodeURIComponent(name)}`)
      .then((res) => res.json())
      .then((data) => setArtifactContent(data.content || "(Empty artifact)"))
      .catch(() => setArtifactContent("(Failed to load artifact)"));
  };

  return (
    <div className="flex-1 flex flex-col md:flex-row bg-[#090e1a] overflow-hidden text-xs">
      {/* Left List */}
      <div className="w-full md:w-80 border-r border-slate-800 bg-[#0c1222] flex flex-col">
        <div className="p-3 border-b border-slate-800 flex items-center justify-between">
          <div className="flex items-center gap-2 font-semibold text-slate-200">
            <FileCode className="w-4 h-4 text-sky-400" />
            <span>Generated Artifacts</span>
          </div>
          <button onClick={refreshArtifacts} className="p-1 hover:bg-slate-800 rounded text-slate-400">
            <RefreshCw className="w-3.5 h-3.5" />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto p-2 space-y-1.5">
          {artifacts.length === 0 ? (
            <div className="p-4 text-slate-500 text-center">No artifacts recorded in this session.</div>
          ) : (
            artifacts.map((a) => (
              <div
                key={a.name}
                onClick={() => handleSelect(a.name)}
                className={`p-2.5 rounded border cursor-pointer transition space-y-1 ${
                  selectedArtifact === a.name
                    ? "bg-sky-500/20 border-sky-500/40 text-white"
                    : "bg-[#090e1a] border-slate-800/80 hover:bg-slate-900 text-slate-300"
                }`}
              >
                <div className="flex items-center justify-between font-mono text-[11px]">
                  <span className="font-semibold text-slate-200 truncate">{a.name}</span>
                  {a.metadata?.UserFacing && (
                    <span className="px-1.5 py-0.2 rounded bg-sky-500/20 text-sky-400 text-[10px]">
                      User
                    </span>
                  )}
                </div>
                {a.metadata?.Summary && (
                  <p className="text-[11px] text-slate-500 line-clamp-2">{a.metadata.Summary}</p>
                )}
              </div>
            ))
          )}
        </div>
      </div>

      {/* Right Content */}
      <div className="flex-1 flex flex-col p-4 bg-[#080d19] overflow-y-auto">
        {selectedArtifact ? (
          <div className="space-y-3">
            <div className="pb-2 border-b border-slate-800 font-mono text-slate-200 text-sm font-semibold flex items-center justify-between">
              <span>{selectedArtifact}</span>
            </div>
            <pre className="p-4 bg-slate-950 border border-slate-800 rounded font-mono text-xs text-slate-200 overflow-x-auto whitespace-pre-wrap leading-relaxed selection:bg-sky-500/30 select-text">
              {artifactContent}
            </pre>
          </div>
        ) : (
          <div className="h-full flex flex-col items-center justify-center text-slate-500">
            <FileText className="w-8 h-8 text-slate-700 mb-2" />
            <p>Select an artifact from the list to preview content.</p>
          </div>
        )}
      </div>
    </div>
  );
};
