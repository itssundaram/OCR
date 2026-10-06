import React, { useState, useEffect } from 'react';
import { FileText, CheckCircle, XCircle, Activity, TrendingUp, Clock } from 'lucide-react';
import { Card, CardContent, CardHeader, CardTitle } from '../components/ui/Card';
import { Badge } from '../components/ui/Badge';
import { Loader } from '../components/ui/Loader';
import { dashboardService } from '../services/dashboard';
import { eventsService } from '../services/events';

const statusVariant = (status) => {
  switch (status) {
    case 'COMPLETED': return 'success';
    case 'WARNING': return 'warning';
    case 'FAILED': return 'danger';
    case 'PROCESSING': return 'info';
    default: return 'default';
  }
};

const StatCard = ({ title, value, icon, delay }) => (
  <Card className="animate-fade-in-up hover-lift" style={{ animationDelay: `${delay}s` }}>
    <CardContent className="flex flex-col gap-2">
      <div className="flex items-center justify-between">
        <span className="text-secondary font-medium">{title}</span>
        <div className="p-2 rounded-lg bg-opacity-10 bg-primary-accent">
          {icon}
        </div>
      </div>
      <div className="text-3xl font-bold font-heading text-primary">{value}</div>
    </CardContent>
  </Card>
);

const Dashboard = () => {
  const [stats, setStats] = useState(null);
  const [events, setEvents] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    (async () => {
      try {
        const [statsRes, eventsRes] = await Promise.all([
          dashboardService.getStats(),
          eventsService.recent({ limit: 8 }),
        ]);
        setStats(statsRes.data);
        setEvents(eventsRes.data || []);
      } catch (err) {
        setError(err.message || 'Failed to load dashboard stats');
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  if (loading) return <div className="h-full flex items-center justify-center"><Loader text="Loading dashboard..." /></div>;
  if (error) return <div className="text-danger p-4 glass-panel">{error}</div>;

  return (
    <div className="flex flex-col gap-8 max-w-7xl mx-auto p-4 w-full">
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 'var(--spacing-lg)' }}>
        <StatCard title="Total Documents" value={stats.total_documents.toLocaleString()} icon={<FileText size={24} className="text-primary-accent" />} delay={0} />
        <StatCard title="Successfully Processed" value={stats.completed_jobs.toLocaleString()} icon={<CheckCircle size={24} style={{ color: 'var(--success)' }} />} delay={0.05} />
        <StatCard title="In Progress" value={stats.in_progress_jobs.toLocaleString()} icon={<Activity size={24} style={{ color: 'var(--warning)' }} />} delay={0.1} />
        <StatCard title="Failed Jobs" value={stats.failed_jobs.toLocaleString()} icon={<XCircle size={24} style={{ color: 'var(--danger)' }} />} delay={0.15} />
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 'var(--spacing-lg)' }}>
        <Card className="animate-fade-in-up" style={{ animationDelay: '0.2s' }}>
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
                WebkitTextFillColor: 'transparent',
              }}>
                {(stats.average_confidence * 100).toFixed(1)}%
              </div>
              <div className="w-full bg-bg-tertiary rounded-pill h-3 overflow-hidden border border-border-color">
                <div
                  className="h-full progress-fill"
                  style={{ width: `${stats.average_confidence * 100}%`, background: 'var(--gradient-primary)' }}
                />
              </div>
              <p className="text-sm text-secondary mt-4 text-center">
                Across all successfully processed documents.
              </p>
            </div>
          </CardContent>
        </Card>

        <Card className="animate-fade-in-up" style={{ animationDelay: '0.25s' }}>
          <CardHeader><CardTitle>Recent Activity</CardTitle></CardHeader>
          <CardContent className="p-0">
            {events.length === 0 ? (
              <div className="p-8 text-center text-secondary italic">No processing events recorded yet.</div>
            ) : (
              <div className="flex flex-col">
                {events.map((e) => (
                  <div key={e.id} className="flex items-center justify-between px-4 py-3 border-b border-border-color border-opacity-50">
                    <div className="flex items-center gap-3">
                      <Clock size={14} className="text-tertiary" />
                      <span className="text-sm text-primary font-medium">{e.event_type}</span>
                    </div>
                    {e.status && <Badge variant={statusVariant(e.status)} className="text-xs">{e.status}</Badge>}
                  </div>
                ))}
              </div>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
};

export default Dashboard;
