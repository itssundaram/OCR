import React from 'react';
import { NavLink, useLocation } from 'react-router-dom';
import {
  LayoutDashboard, Layers, ScanLine, Activity, GitCompare,
  LifeBuoy, FileText, Terminal, Landmark, PlusCircle, Settings, ShieldCheck
} from 'lucide-react';

const Sidebar = ({ isOpen }) => {
  const location = useLocation();

  const navItems = [
    { name: 'Dashboard',                          path: '/dashboard',     icon: <LayoutDashboard size={20} /> },
    { name: 'Manage Departments & Templates',     path: '/manage',        icon: <Layers size={20} /> },
    { name: 'Generate URL / API',                 path: '/generate-url',  icon: <Terminal size={20} /> },
    { name: 'Single OCR & Upload',                path: '/ocr',           icon: <ScanLine size={20} /> },
    { name: 'Extraction Results',                 path: '/results',       icon: <FileText size={20} /> },
    { name: 'System Logs',                        path: '/logs',          icon: <Activity size={20} /> },
    { name: 'Multi-Pipeline Orchestration',       path: '/orchestration', icon: <GitCompare size={20} /> },
    { name: 'Help & Support',                     path: '/help',          icon: <LifeBuoy size={20} /> },
  ];

  return (
    <aside className={`sidebar glass-panel ${!isOpen ? 'collapsed' : ''}`}>
      <div className="p-4 border-b border-border-color/80">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-xl bg-gradient-to-br from-blue-900 to-indigo-950 text-white flex items-center justify-center shadow-md border border-blue-800/40 flex-shrink-0">
            <Landmark size={21} className="text-blue-100" />
          </div>
          <div className="flex flex-col justify-center logo-text transition-opacity duration-200 min-w-0">
            <h1 className="font-bold text-[1.12rem] text-slate-900 font-heading leading-none whitespace-nowrap tracking-tight">
              MISO-OCR
            </h1>
            <span className="text-[0.62rem] font-bold text-slate-500 tracking-wider leading-none mt-1.5 uppercase whitespace-nowrap">
              Ministry of Defence
            </span>
          </div>
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
