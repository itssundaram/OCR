import React from 'react';
import { NavLink, useLocation } from 'react-router-dom';
import {
  LayoutDashboard, Layers, Link2, ScanLine, Activity, GitCompare,
  LifeBuoy,
} from 'lucide-react';

const Sidebar = ({ isOpen }) => {
  const location = useLocation();

  const navItems = [
    { name: 'Dashboard',       path: '/dashboard',     icon: <LayoutDashboard size={20} /> },
    { name: 'Manage',          path: '/manage',        icon: <Layers size={20} /> },
    { name: 'Generate URL',    path: '/generate-url',  icon: <Link2 size={20} /> },
    { name: 'OCR',             path: '/ocr',           icon: <ScanLine size={20} /> },
    { name: 'Logs',            path: '/logs',          icon: <Activity size={20} /> },
    { name: 'Orchestration',   path: '/orchestration', icon: <GitCompare size={20} /> },
    { name: 'Help & Support',  path: '/help',          icon: <LifeBuoy size={20} /> },
  ];

  return (
    <aside className={`sidebar glass-panel ${!isOpen ? 'collapsed' : ''}`}>
      <div className="sidebar-header">
        <div className="logo-container">
          <div className="logo-icon">AI</div>
          <h1 className="logo-text">DocInt</h1>
        </div>
      </div>

      <nav className="sidebar-nav">
        {navItems.map((item, idx) => {
          const isActive = location.pathname.startsWith(item.path);
          return (
            <NavLink
              key={item.path}
              to={item.path}
              className={`nav-link nav-link-animated ${isActive ? 'active' : ''}`}
              style={{ animationDelay: `${idx * 0.04}s` }}
            >
              <span className="nav-icon">{item.icon}</span>
              <span className="nav-text">{item.name}</span>
              {isActive && <div className="active-indicator" />}
            </NavLink>
          );
        })}
      </nav>
    </aside>
  );
};

export default Sidebar;
