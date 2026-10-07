import React from 'react';
import { Routes, Route, Navigate } from 'react-router-dom';
import AppShell from './components/layout/AppShell';

import Dashboard from './pages/Dashboard';
import ManagePage from './pages/ManagePage';
import TemplateEditorPage from './pages/TemplateEditorPage';
import ResultsPage from './pages/ResultsPage';
import GenerateUrlPage from './pages/GenerateUrlPage';
import OcrPage from './pages/OcrPage';
import LogsPage from './pages/LogsPage';
import OrchestrationPage from './pages/OrchestrationPage';
import OrchestrationCanvasPage from './pages/OrchestrationCanvasPage';
import HelpSupportPage from './pages/HelpSupportPage';

function App() {
  return (
    <Routes>
      <Route path="/" element={<AppShell />}>
        <Route index element={<Navigate to="/dashboard" replace />} />
        <Route path="dashboard" element={<Dashboard />} />
        <Route path="manage" element={<ManagePage />} />
        <Route path="manage/template/:id" element={<TemplateEditorPage />} />
        <Route path="results" element={<ResultsPage />} />
        <Route path="generate-url" element={<GenerateUrlPage />} />
        <Route path="ocr" element={<OcrPage />} />
        <Route path="logs" element={<LogsPage />} />
        <Route path="orchestration" element={<OrchestrationPage />} />
        <Route path="orchestration/canvas/:id" element={<OrchestrationCanvasPage />} />
        <Route path="help" element={<HelpSupportPage />} />

        <Route path="*" element={
          <div style={{ height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center', flexDirection: 'column' }}>
            <h1 style={{ fontSize: '4rem', color: 'var(--primary-accent)' }}>404</h1>
            <p>Page not found</p>
          </div>
        } />
      </Route>
    </Routes>
  );
}

export default App;
