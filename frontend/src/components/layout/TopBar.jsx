import React from 'react';
import { Search } from 'lucide-react';
import { useLocation } from 'react-router-dom';

const TopBar = () => {
  const location = useLocation();
  
  // Create a clean title from the path
  const pathParts = location.pathname.split('/').filter(Boolean);
  let title = "Dashboard";
  if (pathParts.length > 0) {
    title = pathParts[0].charAt(0).toUpperCase() + pathParts[0].slice(1);
    if (title === 'Types') title = 'Document Types';
    if (title === 'Upload') title = 'Process Document';
  }

  return (
    <header className="topbar">
      <div className="topbar-left">
        <h2 className="page-title">{title}</h2>
      </div>
      
      <div className="topbar-center">
        <div className="search-container">
          <Search className="search-icon" size={18} />
          <input type="text" placeholder="Search documents, types..." className="search-input" />
        </div>
      </div>
      
      <div className="topbar-right">
      </div>
    </header>
  );
};

export default TopBar;
