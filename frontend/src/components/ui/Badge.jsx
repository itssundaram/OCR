import React from 'react';
import './ui.css';

export const Badge = ({ children, variant = 'default', className = '', ...props }) => {
  const variantClasses = {
    default: 'badge-default',
    success: 'badge-success',
    warning: 'badge-warning',
    danger: 'badge-danger',
    info: 'badge-info'
  };

  return (
    <span className={`badge-pill ${variantClasses[variant]} ${className}`} {...props}>
      {children}
    </span>
  );
};
