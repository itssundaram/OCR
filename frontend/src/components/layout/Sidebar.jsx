import React from 'react';
import { NavLink, useLocation } from 'react-router-dom';
import { LayoutDashboard, FileText, UploadCloud, Database, BookOpen, PlusCircle } from 'lucide-react';

const Sidebar = () => {
  const location = useLocation();
  
  const navItems = [
    { name: 'Dashboard',            path: '/dashboard', icon: <LayoutDashboard size={20} /> },
    { name: 'Documents',            path: '/documents',  icon: <FileText size={20} /> },
    { name: 'Process Document',     path: '/upload',     icon: <UploadCloud size={20} /> },
  ];

  return (
    <aside className="sidebar glass-panel">
      <div className="sidebar-header">
        <div className="logo-container">
          <div className="logo-icon">AI</div>
          <h1 className="logo-text">DocInt</h1>
        </div>
      </div>
      
      <nav className="sidebar-nav">
        {navItems.map((item) => {
          const isActive = location.pathname.startsWith(item.path);
          return (
            <NavLink
              key={item.path}
              to={item.path}
              className={`nav-link ${isActive ? 'active' : ''}`}
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
