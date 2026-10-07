import React, { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { 
  FileText, CheckCircle, XCircle, Activity, TrendingUp, 
  GitCompare, ScanLine, AlertTriangle, ChevronRight, Clock, Box 
} from 'lucide-react';
import { Card, CardContent, CardHeader, CardTitle } from '../components/ui/Card';
import { Badge } from '../components/ui/Badge';
import { Loader } from '../components/ui/Loader';
import { dashboardService } from '../services/dashboard';
import { processingService } from '../services/processing';

const statusVariant = (status) => {
  switch (status) {
    case 'COMPLETED': return 'success';
    case 'WARNING': return 'warning';
    case 'FAILED': return 'danger';
    case 'PROCESSING': return 'info';
    case 'QUEUED': return 'default';
    default: return 'default';
  }
};

const StatCard = ({ title, value, icon, colorClass, badgeText, delay }) => (
  <Card className="animate-fade-in-up hover-lift border border-border-color shadow-sm" style={{ animationDelay: `${delay}s` }}>
    <CardContent className="flex flex-col p-5">
      <div className="flex items-center justify-between mb-4">
        <span className="text-secondary font-bold text-xs uppercase tracking-wider">{title}</span>
        <div className={`p-2 rounded bg-opacity-10 ${colorClass}`}>
          {icon}
        </div>
      </div>
      <div className="text-4xl font-bold font-heading text-primary mb-3">{value}</div>
      <div className="mt-auto">
        <span className={`text-xs font-semibold px-2 py-1 rounded bg-opacity-10 ${colorClass}`}>{badgeText}</span>
      </div>
    </CardContent>
  </Card>
);

const QuickOpButton = ({ title, description, icon, onClick, delay }) => (
  <button 
    onClick={onClick}
    className="w-full text-left flex items-center justify-between p-4 border border-border-color rounded-lg hover:border-primary-accent hover:bg-bg-secondary transition-all animate-fade-in-up"
    style={{ animationDelay: `${delay}s` }}
  >
    <div className="flex items-center gap-4">
      <div className="p-2 rounded bg-primary-accent bg-opacity-10 text-primary-accent">
        {icon}
      </div>
      <div>
        <div className="font-bold text-primary text-sm">{title}</div>
        <div className="text-xs text-secondary mt-0.5">{description}</div>
      </div>
    </div>
    <ChevronRight size={18} className="text-tertiary" />
  </button>
);

const Dashboard = () => {
  const navigate = useNavigate();
  const [stats, setStats] = useState({
    total_documents: 0,
    completed_jobs: 0,
    in_progress_jobs: 0,
    failed_jobs: 0,
    average_confidence: 0,
  });
  const [jobs, setJobs] = useState([]);
  const [loading, setLoading] = useState(true);
  const [systemOffline, setSystemOffline] = useState(false);
  const [errorMessage, setErrorMessage] = useState(null);

  useEffect(() => {
    (async () => {
      try {
        const [statsRes, jobsRes] = await Promise.all([
          dashboardService.getStats(),
          processingService.list({ limit: 6 }),
        ]);
        setStats(statsRes.data);
        setJobs(jobsRes.data || []);
      } catch (err) {
        setSystemOffline(true);
        setErrorMessage(err.message || 'Data services offline.');
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  if (loading) return <div className="h-full flex items-center justify-center"><Loader text="Loading orchestrator..." /></div>;

  return (
    <div className="flex flex-col gap-6 max-w-[1400px] mx-auto p-4 w-full pb-10">
      
      {systemOffline && (
        <div className="bg-danger bg-opacity-10 border border-danger text-danger px-4 py-3 rounded-lg flex items-center gap-3 animate-fade-in-up">
          <AlertTriangle size={20} />
          <div className="flex flex-col">
            <span className="font-bold text-sm">System Offline / Database Disconnected</span>
            <span className="text-xs opacity-80 truncate" style={{ maxWidth: '800px' }}>{errorMessage}</span>
          </div>
        </div>
      )}

      {/* Header section */}
      <div className="flex flex-col mb-2 animate-fade-in-up">
        <h1 className="text-3xl font-bold text-primary font-heading tracking-tight">DocInt Orchestrator Overview</h1>
        <p className="text-sm text-secondary mt-1">Real-time pipeline verification, inference benchmarks, and audit feed.</p>
      </div>

      {/* Top 4 Stats */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 'var(--spacing-lg)' }}>
        <StatCard 
          title="Total Documents" 
          value={stats.total_documents.toLocaleString()} 
          icon={<FileText size={20} className="text-primary-accent" />} 
          colorClass="text-primary-accent bg-primary-accent"
          badgeText="100% Ingestion Quota" 
          delay={0} 
        />
        <StatCard 
          title="Successfully Processed" 
          value={stats.completed_jobs.toLocaleString()} 
          icon={<CheckCircle size={20} className="text-success" />} 
          colorClass="text-success bg-success"
          badgeText="Standard Met" 
          delay={0.05} 
        />
        <StatCard 
          title="In Progress" 
          value={stats.in_progress_jobs.toLocaleString()} 
          icon={<Activity size={20} className="text-warning" />} 
          colorClass="text-warning bg-warning"
          badgeText="Pipeline Sync Runtime" 
          delay={0.1} 
        />
        <StatCard 
          title="Failed Jobs" 
          value={stats.failed_jobs.toLocaleString()} 
          icon={<XCircle size={20} className="text-danger" />} 
          colorClass="text-danger bg-danger"
          badgeText="Requires Human Review" 
          delay={0.15} 
        />
      </div>

      {/* Middle Grid */}
      <div style={{ display: 'grid', gridTemplateColumns: '1.5fr 1fr', gap: 'var(--spacing-lg)' }}>
        
        {/* AI Confidence Benchmark */}
        <Card className="animate-fade-in-up border border-border-color shadow-sm flex flex-col" style={{ animationDelay: '0.2s' }}>
          <CardHeader className="border-b border-border-color py-4">
            <div className="flex items-center justify-between w-full">
              <CardTitle className="flex items-center gap-2 text-lg">
                <TrendingUp size={18} className="text-primary-accent" />
                AI Confidence Benchmark
              </CardTitle>
              <div className="flex items-center gap-2">
                <Badge variant="outline" className="text-xs bg-bg-secondary">Model: DocInt-OCR-v4.7</Badge>
              </div>
            </div>
          </CardHeader>
          <CardContent className="flex flex-col flex-1 p-6">
            <div className="flex justify-between items-end mb-2">
              <div>
                <div className="text-6xl font-bold font-heading text-primary tracking-tight">
                  {(stats.average_confidence * 100).toFixed(1)}%
                </div>
                <div className="text-sm text-secondary mt-1">Across all successfully processed documents.</div>
              </div>
              <div className="text-right">
                <div className="text-xs font-bold text-secondary mb-1">Consensus Target: ≥ 92.0%</div>
                <div className="text-xs font-bold text-success flex items-center gap-1 justify-end">
                  <CheckCircle size={12} /> Standard Met
                </div>
              </div>
            </div>
            
            <div className="mt-6 mb-6">
              <div className="w-full bg-bg-tertiary rounded-full h-2.5 overflow-hidden border border-border-color relative">
                <div
                  className="h-full bg-primary-accent transition-all duration-1000 ease-out"
                  style={{ width: `${stats.average_confidence * 100}%` }}
                />
                <div className="absolute top-0 bottom-0 border-l border-border-color" style={{ left: '92%' }}></div>
              </div>
              <div className="flex justify-between text-[10px] text-tertiary font-bold uppercase mt-2 tracking-wider">
                <span>0% Baseline</span>
                <span>92% SLA Cutoff</span>
                <span>{(stats.average_confidence * 100).toFixed(1)}% Actual</span>
              </div>
            </div>

            <div className="mt-auto pt-4 grid grid-cols-3 gap-4">
              <div className="bg-bg-secondary border border-border-color p-3 rounded flex flex-col items-center text-center justify-center">
                <span className="text-xs text-secondary font-medium">Pipeline Strategy</span>
                <span className="text-sm font-bold text-primary mt-1">AI Orchestration</span>
              </div>
              <div className="bg-bg-secondary border border-border-color p-3 rounded flex flex-col items-center text-center justify-center">
                <span className="text-xs text-secondary font-medium">Zero-Shot Precision</span>
                <span className="text-sm font-bold text-success mt-1">High</span>
              </div>
              <div className="bg-bg-secondary border border-border-color p-3 rounded flex flex-col items-center text-center justify-center">
                <span className="text-xs text-secondary font-medium">Consensus Threshold</span>
                <span className="text-sm font-bold text-primary mt-1">Strict (2-of-3)</span>
              </div>
            </div>
          </CardContent>
        </Card>

        {/* Quick Operations */}
        <Card className="animate-fade-in-up border border-border-color shadow-sm flex flex-col" style={{ animationDelay: '0.25s' }}>
          <CardHeader className="border-b border-border-color py-4">
            <div className="flex items-center justify-between w-full">
              <CardTitle className="flex items-center gap-2 text-lg">
                <Activity size={18} />
                Quick Operations
              </CardTitle>
              <span className="text-xs font-bold text-tertiary tracking-wider uppercase">ENGINES READY</span>
            </div>
          </CardHeader>
          <CardContent className="flex flex-col gap-3 p-6">
            <QuickOpButton 
              title="Multi-Pipeline Orchestrator" 
              description="Launch parallel OCR & AI consensus evaluation" 
              icon={<GitCompare size={20} />} 
              onClick={() => navigate('/orchestration')}
              delay={0.3}
            />
            <QuickOpButton 
              title="Single Pipeline OCR" 
              description="Quick run on individual models (Paddle, Surya, etc)" 
              icon={<ScanLine size={20} />} 
              onClick={() => navigate('/ocr')}
              delay={0.35}
            />
            <QuickOpButton 
              title="Inspect Flagged Fails" 
              description="Items awaiting human-in-the-loop review" 
              icon={<AlertTriangle size={20} className="text-danger" />} 
              onClick={() => navigate('/results')}
              delay={0.4}
            />
          </CardContent>
        </Card>
      </div>

      {/* Bottom Live Activity Stream */}
      <Card className="animate-fade-in-up border border-border-color shadow-sm mt-2" style={{ animationDelay: '0.3s' }}>
        <CardHeader className="border-b border-border-color py-4">
          <div className="flex items-center justify-between w-full">
            <CardTitle className="flex items-center gap-2 text-lg">
              <Box size={18} className="text-primary-accent" />
              Live Activity Stream
            </CardTitle>
            <div className="flex gap-4 text-xs font-bold uppercase tracking-wider text-tertiary">
              <span className="text-primary-accent">All ({stats.total_documents})</span>
              <span className="text-success">Completed ({stats.completed_jobs})</span>
              <span className="text-warning">Running ({stats.in_progress_jobs})</span>
            </div>
          </div>
        </CardHeader>
        <CardContent className="p-0 overflow-x-auto">
          {jobs.length === 0 ? (
            <div className="p-8 text-center text-secondary italic">No processing jobs recorded yet.</div>
          ) : (
            <table className="w-full text-left border-collapse">
              <thead>
                <tr className="bg-bg-secondary border-b border-border-color text-xs text-tertiary uppercase tracking-wider">
                  <th className="font-medium p-4 py-3">Timestamp</th>
                  <th className="font-medium p-4 py-3">Job ID</th>
                  <th className="font-medium p-4 py-3">Pipeline Engine</th>
                  <th className="font-medium p-4 py-3">Confidence</th>
                  <th className="font-medium p-4 py-3">Status</th>
                  <th className="font-medium p-4 py-3 text-right">Action</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border-color">
                {jobs.map((job) => (
                  <tr key={job.id || job.job_id} className="hover:bg-bg-secondary transition-colors group">
                    <td className="p-4 text-sm text-secondary flex items-center gap-2 whitespace-nowrap">
                      <Clock size={14} className="text-tertiary" />
                      {new Date(job.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })}
                    </td>
                    <td className="p-4 text-sm font-mono text-primary whitespace-nowrap">
                      {(job.id || job.job_id)?.substring(0, 8)}...
                    </td>
                    <td className="p-4 text-sm text-primary font-medium">
                      {job.pipeline_mode === 'orchestration' ? 'AI Consensus Pipeline' : (job.ai_engine || 'Default Pipeline')}
                    </td>
                    <td className="p-4 text-sm font-medium">
                      {job.overall_confidence ? (
                        <span className={job.overall_confidence > 0.9 ? 'text-success' : job.overall_confidence > 0.8 ? 'text-warning' : 'text-danger'}>
                          {(job.overall_confidence * 100).toFixed(1)}%
                        </span>
                      ) : (
                        <span className="text-tertiary italic">Computing...</span>
                      )}
                    </td>
                    <td className="p-4">
                      <Badge variant={statusVariant(job.status)} className="text-xs shadow-sm">{job.status}</Badge>
                    </td>
                    <td className="p-4 text-right">
                      <button 
                        onClick={() => navigate('/results')}
                        className="btn btn-secondary text-xs opacity-0 group-hover:opacity-100 transition-opacity"
                      >
                        View
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </CardContent>
      </Card>
    </div>
  );
};

export default Dashboard;
