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
    conversationName,
    pendingApproval,
    pendingQuestion,
    sidecarMessages,
    reasoningEffort,
    hasActiveKnowledgeWorkers,
    sendPrompt,
    sendContinue,
    sendSteer,
    sendInterrupt,
    sendApproval,
    sendModalAnswer,
    sendSidecar,
    resetSession,
    loadConversation,
    compactContext,
    undoMessage,
    stopKnowledgeWorkers,
    setReasoningEffort,
    renameConversation,
  } = useWebSocket();

  const [restoredText, setRestoredText] = useState<string>("");

  const handleUndo = (msg: any, displayContent: string) => {
    const textToRestore = msg.raw_prompt || displayContent;
    setRestoredText(textToRestore);
    undoMessage(msg.id ?? msg.message_index ?? 0);
  };

  return (
    <div className="flex flex-col h-screen w-screen bg-[#090d16] text-slate-100 overflow-hidden">
      {/* Top Header */}
      <Header
        connected={connected}
        projectId={projectId}
        conversationId={conversationId}
        conversationName={conversationName}
        currentPhase={currentPhase}
        totalTokens={totalTokens}
        reasoningEffort={reasoningEffort}
        onReasoningEffortChange={setReasoningEffort}
        onReset={resetSession}
        onCompact={compactContext}
        onSelectConversation={loadConversation}
        onRenameConversation={renameConversation}
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
              onUndo={handleUndo}
            />
            <InputBar
              isStreaming={isStreaming}
              hasActiveKnowledgeWorkers={hasActiveKnowledgeWorkers}
              onSend={sendPrompt}
              onContinue={sendContinue}
              onInterrupt={() => sendInterrupt("paused_by_user")}
              onStopKnowledgeWorkers={stopKnowledgeWorkers}
              onSteer={sendSteer}
              onOpenSidecar={() => setActiveTab("sidecar")}
              restoredText={restoredText}
              onClearRestoredText={() => setRestoredText("")}
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
