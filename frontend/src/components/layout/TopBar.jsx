import React from 'react';
import { Search, Menu, Bell, Mail, RefreshCw, Server, Zap, ShieldAlert, Rocket } from 'lucide-react';
import { useLocation } from 'react-router-dom';

const TopBar = ({ toggleSidebar }) => {
  const location = useLocation();

  return (
    <header className="topbar flex items-center justify-between border-b border-border-color bg-white w-full h-[60px] px-4">
      <div className="flex items-center gap-4 flex-1">
        <button onClick={toggleSidebar} className="text-secondary hover:text-primary transition-colors bg-transparent border-none cursor-pointer">
          <Menu size={20} />
        </button>
        <div className="search-container flex items-center bg-bg-tertiary px-3 py-1.5 rounded-md border border-border-color max-w-[400px] w-full">
          <Search className="text-tertiary mr-2" size={16} />
          <input type="text" placeholder="Search documents, executions, templates, audit logs" className="bg-transparent border-none outline-none text-sm w-full text-primary placeholder-tertiary font-medium" />
        </div>
      </div>

      <div className="flex items-center gap-4">
        <div className="w-8 h-8 rounded-full bg-border-color border border-border-color overflow-hidden flex items-center justify-center cursor-pointer">
          <ShieldAlert size={18} className="text-tertiary" />
        </div>
      </div>
    </header>
  );
};

export default TopBar;
