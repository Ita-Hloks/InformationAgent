import { Navigate, Route, Routes } from "react-router-dom";

import { ReaderWorkspace } from "../features/readerWorkspace";

export function AppRoutes() {
  return (
    <Routes>
      <Route element={<ReaderWorkspace />} path="/">
        <Route element={<Navigate replace to="/inbox" />} index />
        <Route element={<WorkspaceRoute />} path="inbox" />
        <Route element={<WorkspaceRoute />} path="today" />
        <Route element={<WorkspaceRoute />} path="all" />
        <Route element={<WorkspaceRoute />} path="saved" />
        <Route element={<WorkspaceRoute />} path="research" />
        <Route element={<WorkspaceRoute />} path="feeds/:feedId" />
        <Route element={<Navigate replace to="/inbox" />} path="*" />
      </Route>
    </Routes>
  );
}

function WorkspaceRoute() {
  return null;
}
