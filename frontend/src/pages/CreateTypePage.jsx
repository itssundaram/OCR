/**
 * Step 1: Select Department
 * User picks a department from those available in the DB.
 */
import React, { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { departmentsService } from '../services/departments';
import { Loader } from '../components/ui/Loader';

const ICON_MAP = {
  'dollar-sign': '💰', 'users': '👥', 'scale': '⚖️', 'truck': '🚚',
  'heart': '🩺', 'folder': '📁', 'file': '📄', 'settings': '⚙️',
};

const Stepper = ({ step }) => {
  const steps = ['Department', 'Template Info', 'Schema', 'Endpoints'];
  return (
    <div className="wizard-stepper">
      {steps.map((label, i) => {
        const status = i < step ? 'done' : i === step ? 'active' : 'pending';
        return (
          <React.Fragment key={i}>
            {i > 0 && <div className={`wizard-step-line ${i <= step ? 'done' : ''}`} />}
            <div className="wizard-step">
              <div className={`wizard-step-circle ${status}`}>
                {status === 'done' ? '✓' : i + 1}
              </div>
              <span className={`wizard-step-label ${status}`}>{label}</span>
            </div>
          </React.Fragment>
        );
      })}
    </div>
  );
};

export default function CreateTypePage() {
  const navigate = useNavigate();
  const [departments, setDepartments] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  
  // Create Modal State
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [newDeptName, setNewDeptName] = useState('');
  const [newDeptDesc, setNewDeptDesc] = useState('');
  const [isCreating, setIsCreating] = useState(false);

  useEffect(() => {
    departmentsService.list()
      .then(res => setDepartments(res.data || []))
      .catch(e => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  const handleSelect = (dept) => {
    navigate('/create/doc-type', { state: { department: dept } });
  };

  const handleCreateDepartment = async (e) => {
    e.preventDefault();
    if (!newDeptName.trim()) return;

    setIsCreating(true);
    const slug = newDeptName.trim().toUpperCase().replace(/[^A-Z0-9]/g, '_');
    
    try {
      const res = await departmentsService.create({
        slug,
        name: newDeptName.trim(),
        description: newDeptDesc.trim() || undefined
      });
      setDepartments([...departments, res.data]);
      setShowCreateModal(false);
      setNewDeptName('');
      setNewDeptDesc('');
    } catch (err) {
      alert(err.message || 'Failed to create department');
    } finally {
      setIsCreating(false);
    }
  };

  return (
    <div className="wizard-page">
      <div className="wizard-header">
        <button
          onClick={() => navigate('/')}
          className="btn btn-secondary"
          style={{ marginBottom: 'var(--spacing-lg)' }}
        >
          ← Back to Dashboard
        </button>
        <h1 style={{ fontFamily: 'var(--font-heading)', fontSize: '1.8rem', marginBottom: 'var(--spacing-xs)' }}>
          Create Document Type
        </h1>
        <p style={{ color: 'var(--text-secondary)' }}>
          Step 1 of 4 — Choose the department this document type belongs to.
        </p>
        <Stepper step={0} />
      </div>

      {loading && <Loader />}
      {error && (
        <div className="document-types-error" style={{ maxWidth: 900, width: '100%' }}>
          Failed to load departments: {error}
        </div>
      )}

      {!loading && !error && (
        <div className="dept-grid">
          {departments.map(dept => (
            <div
              key={dept.slug}
              className="dept-card"
              style={{ '--dept-color': dept.color }}
              onClick={() => handleSelect(dept)}
              role="button"
              aria-label={`Select ${dept.name}`}
            >
              <div className="dept-card-icon" style={{ background: `${dept.color}20`, color: dept.color }}>
                {ICON_MAP[dept.icon] || '📁'}
              </div>
              <p className="dept-card-name">{dept.name}</p>
              {dept.description && <p className="dept-card-desc">{dept.description}</p>}
            </div>
          ))}

          {/* Create New Department Card */}
          <div
            className="dept-card new-dept-card"
            style={{ '--dept-color': 'var(--text-secondary)' }}
            onClick={() => setShowCreateModal(true)}
            role="button"
            aria-label="Create New Department"
          >
            <div className="dept-card-icon" style={{ background: 'var(--bg-card-hover)', color: 'var(--text-secondary)' }}>
              ➕
            </div>
            <p className="dept-card-name">Create New Department</p>
            <p className="dept-card-desc">Add a new department workspace.</p>
          </div>
        </div>
      )}

      {/* Create Modal */}
      {showCreateModal && (
        <div className="modal-overlay" onClick={() => setShowCreateModal(false)}>
          <div className="modal-content" onClick={e => e.stopPropagation()}>
            <h2 style={{ marginBottom: 'var(--spacing-md)' }}>Create New Department</h2>
            <form onSubmit={handleCreateDepartment}>
              <div className="form-group">
                <label>Department Name <span style={{color: 'red'}}>*</span></label>
                <input 
                  type="text" 
                  className="form-control" 
                  value={newDeptName}
                  onChange={e => setNewDeptName(e.target.value)}
                  placeholder="e.g. Finance"
                  required
                  autoFocus
                />
              </div>
              <div className="form-group">
                <label>Description (Optional)</label>
                <textarea 
                  className="form-control"
                  value={newDeptDesc}
                  onChange={e => setNewDeptDesc(e.target.value)}
                  placeholder="Brief description of documents processed here..."
                  rows={3}
                />
              </div>
              <div style={{ display: 'flex', gap: 'var(--spacing-sm)', justifyContent: 'flex-end', marginTop: 'var(--spacing-lg)' }}>
                <button type="button" className="btn btn-secondary" onClick={() => setShowCreateModal(false)}>
                  Cancel
                </button>
                <button type="submit" className="btn btn-primary" disabled={isCreating || !newDeptName.trim()}>
                  {isCreating ? 'Creating...' : 'Create'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
