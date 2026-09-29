import React from 'react';
import { Loader2 } from 'lucide-react';

export const Loader = ({ size = 24, text = '', className = '' }) => {
  return (
    <div className={`flex flex-col items-center justify-center gap-2 ${className}`}>
      <Loader2 size={size} className="animate-spin text-primary-accent" style={{ animation: 'spin 1s linear infinite' }} />
      {text && <span className="text-sm text-secondary">{text}</span>}
    </div>
  );
};
