import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AuthProvider, RequireAuth } from "./auth";
import { Layout } from "./components/Layout";
import Login from "./pages/Login";
import Dashboard from "./pages/Dashboard";
import Upload from "./pages/Upload";
import Sources from "./pages/Sources";
import Glossary from "./pages/Glossary";
import RouteAConfirm from "./pages/RouteAConfirm";
import SingleSubmit from "./pages/SingleSubmit";
import TranscriptViewer from "./pages/TranscriptViewer";
import Settings from "./pages/Settings";
import "./index.css";

const qc = new QueryClient({
  defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
});

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <QueryClientProvider client={qc}>
      <BrowserRouter>
        <AuthProvider>
          <Routes>
            <Route path="/login" element={<Login />} />
            <Route
              element={
                <RequireAuth>
                  <Layout />
                </RequireAuth>
              }
            >
              <Route path="/" element={<Dashboard />} />
              <Route path="/upload" element={<Upload />} />
              <Route path="/sources" element={<Sources />} />
              <Route path="/glossary" element={<Glossary />} />
              <Route path="/recordings/:recId/confirm" element={<RouteAConfirm />} />
              <Route path="/recordings/:recId/single" element={<SingleSubmit />} />
              <Route path="/jobs/:jobId" element={<TranscriptViewer />} />
              <Route path="/settings" element={<Settings />} />
            </Route>
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </AuthProvider>
      </BrowserRouter>
    </QueryClientProvider>
  </React.StrictMode>,
);
