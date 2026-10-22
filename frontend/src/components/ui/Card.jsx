import React from 'react';
import './ui.css';

export const Card = ({ children, className = '', ...props }) => {
  return (
    <div className={`bg-white border border-slate-200 rounded-xl shadow-2xs ${className}`} {...props}>
      {children}
    </div>
  );
};

export const CardHeader = ({ children, className = '', ...props }) => {
  return (
    <div className={`p-4 border-b border-slate-100 ${className}`} {...props}>
      {children}
    </div>
  );
};

export const CardTitle = ({ children, className = '', ...props }) => {
  return (
    <h3 className={`font-bold text-base text-slate-900 m-0 ${className}`} {...props}>
      {children}
    </h3>
  );
};

export const CardContent = ({ children, className = '', ...props }) => {
  return (
    <div className={`p-4 ${className}`} {...props}>
      {children}
    </div>
  );
};
