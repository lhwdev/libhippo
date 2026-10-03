import React, { useState } from "react";
import { Header } from "./components/layout/Header";
import { TabNav, NavTab } from "./components/layout/TabNav";
import { ChatTimeline } from "./components/chat/ChatTimeline";
import { InputBar } from "./components/chat/InputBar";
import { SidecarDrawer } from "./components/sidecar/SidecarDrawer";
import { SettingsView } from "./components/settings/SettingsView";
import { TaskMonitor } from "./components/tasks/TaskMonitor";
import { KnowledgeExplorer } from "./components/knowledge/KnowledgeExplorer";
import { ArtifactsGallery } from "./components/artifacts/ArtifactsGallery";
import { useWebSocket } from "./hooks/useWebSocket";

export const App: React.FC = () => {
  const [activeTab, setActiveTab] = useState<NavTab>("chat");

  const {
    connected,
    messages,
    isStreaming,
    streamingResponse,
    currentPhase,
    totalTokens,
    projectId,
    conversationId,
    pendingApproval,
    pendingQuestion,
    sidecarMessages,
    sendPrompt,
    sendSteer,
    sendInterrupt,
    sendApproval,
    sendModalAnswer,
    sendSidecar,
    resetSession,
    compactContext,
  } = useWebSocket();

  return (
    <div className="flex flex-col h-screen w-screen bg-[#090d16] text-slate-100 overflow-hidden">
      {/* Top Header */}
      <Header
        connected={connected}
        projectId={projectId}
        conversationId={conversationId}
        currentPhase={currentPhase}
        totalTokens={totalTokens}
        onReset={resetSession}
        onCompact={compactContext}
      />

      {/* Navigation Tabs */}
      <TabNav
        activeTab={activeTab}
        onTabChange={setActiveTab}
        sidecarBadgeCount={sidecarMessages.length}
      />

      {/* Main Viewport */}
      <main className="flex-1 flex flex-col overflow-hidden relative">
        {activeTab === "chat" && (
          <div className="flex-1 flex flex-col overflow-hidden">
            <ChatTimeline
              messages={messages}
              isStreaming={isStreaming}
              streamingResponse={streamingResponse}
              pendingApproval={pendingApproval}
              pendingQuestion={pendingQuestion}
              onRespondApproval={sendApproval}
              onSubmitQuestion={sendModalAnswer}
            />
            <InputBar
              isStreaming={isStreaming}
              onSend={sendPrompt}
              onInterrupt={() => sendInterrupt("paused_by_user")}
              onSteer={sendSteer}
              onOpenSidecar={() => setActiveTab("sidecar")}
            />
          </div>
        )}

        {activeTab === "sidecar" && (
          <SidecarDrawer
            messages={sidecarMessages}
            onSendQuery={sendSidecar}
          />
        )}

        {activeTab === "settings" && <SettingsView />}

        {activeTab === "knowledge" && <KnowledgeExplorer />}

        {activeTab === "tasks" && <TaskMonitor />}

        {activeTab === "artifacts" && <ArtifactsGallery />}
      </main>
    </div>
  );
};

export default App;
