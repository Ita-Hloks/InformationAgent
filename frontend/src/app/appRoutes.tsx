import { Navigate, Route, Routes } from "react-router-dom";

import { SettingsPage } from "../components/appShell";
import { ReaderWorkspacePage } from "../components/readerWorkspace";

export function AppRoutes() {
  return (
    <Routes>
      <Route element={<ReaderWorkspacePage />} path="/">
        <Route element={<Navigate replace to="/inbox" />} index />
        <Route element={<WorkspaceRoute />} path="inbox" />
        <Route element={<WorkspaceRoute />} path="today" />
        <Route element={<WorkspaceRoute />} path="all" />
        <Route element={<WorkspaceRoute />} path="saved" />
        <Route element={<WorkspaceRoute />} path="research" />
        <Route element={<WorkspaceRoute />} path="feeds/:feedId" />
        <Route element={<SettingsPage />} path="settings" />
        <Route element={<Navigate replace to="/inbox" />} path="*" />
      </Route>
    </Routes>
  );
}

function WorkspaceRoute() {
  return null;
}
