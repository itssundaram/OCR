import React from 'react';
import { Routes, Route, Navigate } from 'react-router-dom';
import AppShell from './components/layout/AppShell';

import Dashboard from './pages/Dashboard';
import Documents from './pages/Documents';
import DocumentDetail from './pages/DocumentDetail';
import Upload from './pages/Upload';

// Wizard & Browse pages — lazy loaded, rendered WITHOUT the AppShell sidebar
const CreateTypePage    = React.lazy(() => import('./pages/CreateTypePage'));
const CreateDocTypePage = React.lazy(() => import('./pages/CreateDocTypePage'));
const CreateFieldsPage  = React.lazy(() => import('./pages/CreateFieldsPage'));
const EndpointViewPage  = React.lazy(() => import('./pages/EndpointViewPage'));
const BrowseTemplatesPage  = React.lazy(() => import('./pages/BrowseTemplatesPage'));
const BrowseTypeDetailPage = React.lazy(() => import('./pages/BrowseTypeDetailPage'));

const WizardShell = ({ children }) => (
  <React.Suspense fallback={
    <div style={{ height: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
      <div style={{ color: 'var(--text-secondary)' }}>Loading...</div>
    </div>
  }>
    {children}
  </React.Suspense>
);

function App() {
  return (
    <Routes>
      {/* ── Wizard & Browse routes — no sidebar ── */}
      <Route path="/create" element={<WizardShell><CreateTypePage /></WizardShell>} />
      <Route path="/create/doc-type" element={<WizardShell><CreateDocTypePage /></WizardShell>} />
      <Route path="/create/fields" element={<WizardShell><CreateFieldsPage /></WizardShell>} />
      <Route path="/create/endpoints" element={<WizardShell><EndpointViewPage /></WizardShell>} />
      <Route path="/browse" element={<WizardShell><BrowseTemplatesPage /></WizardShell>} />
      <Route path="/browse/:deptSlug/:typeCode" element={<WizardShell><BrowseTypeDetailPage /></WizardShell>} />

      {/* ── Main shell with sidebar & topbar ── */}
      <Route path="/" element={<AppShell />}>
        <Route index element={<Navigate to="/dashboard" replace />} />
        <Route path="dashboard" element={<Dashboard />} />
        <Route path="documents" element={<Documents />} />
        <Route path="documents/:id" element={<DocumentDetail />} />
        <Route path="upload" element={<Upload />} />

        {/* Catch-all 404 */}
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
