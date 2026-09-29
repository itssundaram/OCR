import React, { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { FileText, CheckCircle, XCircle, Activity, TrendingUp } from 'lucide-react';
import { Card, CardContent, CardHeader, CardTitle } from '../components/ui/Card';
import { Loader } from '../components/ui/Loader';
import { dashboardService } from '../services/dashboard';

const StatCard = ({ title, value, icon, colorClass, subtitle }) => (
  <Card className="animate-fade-in">
    <CardContent className="flex flex-col gap-2">
      <div className="flex items-center justify-between">
        <span className="text-secondary font-medium">{title}</span>
        <div className={`p-2 rounded-lg bg-opacity-10 ${colorClass}`}>
          {icon}
        </div>
      </div>
      <div className="text-3xl font-bold font-heading text-primary">{value}</div>
      {subtitle && <div className="text-sm text-tertiary mt-1">{subtitle}</div>}
    </CardContent>
  </Card>
);

const Dashboard = () => {
  const navigate = useNavigate();
  const [stats, setStats] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    const fetchStats = async () => {
      try {
        const response = await dashboardService.getStats();
        setStats(response.data);
      } catch (err) {
        setError(err.message || "Failed to load dashboard stats");
      } finally {
        setLoading(false);
      }
    };
    fetchStats();
  }, []);

  if (loading) return <div className="h-full flex items-center justify-center"><Loader text="Loading dashboard..." /></div>;
  if (error) return <div className="text-danger p-4 glass-panel">{error}</div>;

  return (
    <div className="flex flex-col gap-8 max-w-7xl mx-auto p-4 w-full">
        {/* ── Hero CTAs ── */}
        <div className="dashboard-hero">
          <div
            className="hero-cta-card create"
            onClick={() => navigate('/create')}
            role="button"
            aria-label="Create a new document type"
          >
            <div className="hero-cta-icon">
              <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M12 5v14M5 12h14" />
              </svg>
            </div>
            <h2 className="hero-cta-title">Create Document Type</h2>
            <p className="hero-cta-desc">
              Define a new extraction template — choose department, type, and specify all fields to extract.
            </p>
            <div className="hero-cta-arrow">→</div>
          </div>

          <div
            className="hero-cta-card browse"
            onClick={() => navigate('/browse')}
            role="button"
            aria-label="Browse existing templates"
          >
            <div className="hero-cta-icon">
              <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M9 12h6M9 16h6M9 8h6M5 3h14a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2Z" />
              </svg>
            </div>
            <h2 className="hero-cta-title">Browse Templates</h2>
            <p className="hero-cta-desc">
              View existing document types, see their field definitions, and copy API endpoints to your clipboard.
            </p>
            <div className="hero-cta-arrow">→</div>
          </div>
        </div>

        {/* ── Stats Grid ── */}
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 'var(--spacing-lg)' }}>
          <StatCard
            title="Total Documents"
            value={stats.total_documents.toLocaleString()}
            icon={<FileText size={24} className="text-primary-accent" />}
            colorClass="bg-primary-accent"
          />
          <StatCard
            title="Successfully Processed"
            value={stats.completed_jobs.toLocaleString()}
            icon={<CheckCircle size={24} style={{ color: 'var(--success)' }} />}
            colorClass="bg-success"
          />
          <StatCard
            title="In Progress"
            value={stats.in_progress_jobs.toLocaleString()}
            icon={<Activity size={24} style={{ color: 'var(--warning)' }} />}
            colorClass="bg-warning"
          />
          <StatCard
            title="Failed Jobs"
            value={stats.failed_jobs.toLocaleString()}
            icon={<XCircle size={24} style={{ color: 'var(--danger)' }} />}
            colorClass="bg-danger"
          />
        </div>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 'var(--spacing-lg)' }}>
        <Card className="animate-fade-in" style={{ animationDelay: '0.1s' }}>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <TrendingUp size={20} className="text-primary-accent" />
              AI Confidence Average
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="flex flex-col items-center justify-center py-6">
              <div className="text-5xl font-bold font-heading mb-4" style={{
                background: 'var(--gradient-primary)',
                WebkitBackgroundClip: 'text',
                WebkitTextFillColor: 'transparent'
              }}>
                {(stats.average_confidence * 100).toFixed(1)}%
              </div>
              <div className="w-full bg-bg-tertiary rounded-pill h-3 overflow-hidden border border-border-color">
                <div
                  className="h-full bg-primary-accent"
                  style={{ width: `${stats.average_confidence * 100}%`, background: 'var(--gradient-primary)' }}
                />
              </div>
              <p className="text-sm text-secondary mt-4 text-center">
                Across all successfully processed documents.
              </p>
            </div>
          </CardContent>
        </Card>

        <Card className="animate-fade-in" style={{ animationDelay: '0.2s' }}>
          <CardHeader>
            <CardTitle>System Health</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="flex flex-col gap-4">
              <div className="flex items-center justify-between p-3 rounded-lg border border-border-color bg-bg-secondary">
                <div className="flex items-center gap-3">
                  <div className="w-3 h-3 rounded-pill" style={{ background: 'var(--success)', boxShadow: '0 0 10px var(--success)' }}></div>
                  <span className="font-medium">Oracle Database</span>
                </div>
                <span className="text-success text-sm font-bold">ONLINE</span>
              </div>
              <div className="flex items-center justify-between p-3 rounded-lg border border-border-color bg-bg-secondary">
                <div className="flex items-center gap-3">
                  <div className="w-3 h-3 rounded-pill" style={{ background: 'var(--success)', boxShadow: '0 0 10px var(--success)' }}></div>
                  <span className="font-medium">Redis Queue</span>
                </div>
                <span className="text-success text-sm font-bold">ONLINE</span>
              </div>
              <div className="flex items-center justify-between p-3 rounded-lg border border-border-color bg-bg-secondary">
                <div className="flex items-center gap-3">
                  <div className="w-3 h-3 rounded-pill" style={{ background: 'var(--success)', boxShadow: '0 0 10px var(--success)' }}></div>
                  <span className="font-medium">AI Extraction Engine</span>
                </div>
                <span className="text-success text-sm font-bold">ONLINE</span>
              </div>
            </div>
          </CardContent>
        </Card>
      </div>

    </div>
  );
};

export default Dashboard;
