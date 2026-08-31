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
import { LoginPage } from "./pages/LoginPage";
import { useAuthStore } from "./stores/authStore";
import { useLiveStore } from "./stores/liveStore";

export default function App() {
  const handleFrame = useLiveStore((s) => s.handleFrame);
  const status = useAuthStore((s) => s.status);
  const loading = useAuthStore((s) => s.loading);
  const refresh = useAuthStore((s) => s.refresh);
  useHealthPoll();

  useEffect(() => {
    void refresh();
  }, [refresh]);

  // Only open the live feed once we are allowed to; otherwise the socket is
  // just rejected at handshake.
  const authed = status ? status.authenticated : false;
  useEffect(() => {
    if (!authed) return;
    return connectEvents(handleFrame);
  }, [handleFrame, authed]);

  if (loading) {
    return (
      <div className="flex h-full items-center justify-center font-mono text-xs text-faint">
        starting…
      </div>
    );
  }

  // `status` is null only when /api/auth/status itself failed (backend down).
  // Render the app shell in that case so the existing backend-down UI shows.
  if (status && status.configured && !status.authenticated) {
    return <LoginPage configured />;
  }

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
