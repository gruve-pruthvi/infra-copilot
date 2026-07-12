import { useEffect } from "react";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { connectEvents } from "./api/ws";
import { ApprovalToast } from "./components/ApprovalToast";
import { Sidebar } from "./components/Sidebar";
import { useHealthPoll } from "./hooks/useHealthPoll";
import { ChatPage } from "./pages/ChatPage";
import { DashboardPage } from "./pages/DashboardPage";
import { HistoryPage } from "./pages/HistoryPage";
import { LogsPage } from "./pages/LogsPage";
import { SettingsPage } from "./pages/SettingsPage";
import { useLiveStore } from "./stores/liveStore";

export default function App() {
  const handleFrame = useLiveStore((s) => s.handleFrame);
  useHealthPoll();

  useEffect(() => connectEvents(handleFrame), [handleFrame]);

  return (
    <BrowserRouter>
      <div className="flex h-full">
        <Sidebar />
        <main className="min-w-0 flex-1">
          <Routes>
            <Route path="/" element={<ChatPage />} />
            <Route path="/dashboard" element={<DashboardPage />} />
            <Route path="/logs" element={<LogsPage />} />
            <Route path="/history" element={<HistoryPage />} />
            <Route path="/settings" element={<SettingsPage />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </main>
        <ApprovalToast />
      </div>
    </BrowserRouter>
  );
}
