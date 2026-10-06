import React, { useState } from 'react';
import { Outlet } from 'react-router-dom';
import Sidebar from './Sidebar';
import TopBar from './TopBar';
import './layout.css';

const AppShell = () => {
  const [isSidebarOpen, setIsSidebarOpen] = useState(true);
  const toggleSidebar = () => setIsSidebarOpen(!isSidebarOpen);

  return (
    <div className="app-container">
      {/* Abstract glassmorphism background shapes */}
      <div className="bg-blob blob-1"></div>
      <div className="bg-blob blob-2"></div>
      
      <Sidebar isOpen={isSidebarOpen} />
      
      <div className="main-wrapper">
        <TopBar toggleSidebar={toggleSidebar} />
        <main className="main-content">
          <Outlet />
        </main>
      </div>
    </div>
  );
};

export default AppShell;
