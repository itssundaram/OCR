import React from 'react';
import { Search, Menu } from 'lucide-react';
import { useLocation } from 'react-router-dom';

const TITLES = {
  dashboard: 'Dashboard',
  manage: 'Manage Departments & Templates',
  'generate-url': 'Generate URL',
  ocr: 'OCR — Single Pipeline',
  logs: 'Logs',
  orchestration: 'Orchestration',
  help: 'Help & Support',
};

const TopBar = ({ toggleSidebar }) => {
  const location = useLocation();

  const pathParts = location.pathname.split('/').filter(Boolean);
  const title = TITLES[pathParts[0]] || 'DocInt';

  return (
    <header className="topbar">
      <div className="topbar-left" style={{ display: 'flex', alignItems: 'center', gap: '1rem' }}>
        <button onClick={toggleSidebar} style={{ background: 'transparent', border: 'none', cursor: 'pointer', color: 'var(--text-secondary)', display: 'flex' }}>
          <Menu size={24} />
        </button>
        <h2 className="page-title">{title}</h2>
      </div>

      <div className="topbar-center">
        <div className="search-container">
          <Search className="search-icon" size={18} />
          <input type="text" placeholder="Search documents, templates..." className="search-input" />
        </div>
      </div>

      <div className="topbar-right" />
    </header>
  );
};

export default TopBar;
