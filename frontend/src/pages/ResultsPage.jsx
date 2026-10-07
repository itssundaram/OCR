import React, { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { FileText, Eye, CheckCircle2, XCircle, Clock, GitCompare, RefreshCw, Code, Flag, Database, Filter } from 'lucide-react';
import { processingService } from '../services/processing';
import { documentsService } from '../services/documents';
import { Card, CardContent, CardHeader, CardTitle } from '../components/ui/Card';
import { Badge } from '../components/ui/Badge';
import { Loader } from '../components/ui/Loader';
import api from '../services/api';
import ExtractionResultView from '../components/ExtractionResultView';

const ResultsPage = () => {
  const navigate = useNavigate();
  const [jobs, setJobs] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  
  const [selectedJob, setSelectedJob] = useState(null);
  const [jobDetails, setJobDetails] = useState(null);
  const [loadingDetails, setLoadingDetails] = useState(false);

  const loadJobs = async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await processingService.list({ limit: 50 });
      setJobs(res.data || []);
    } catch (err) {
      setError(err.message || 'Failed to load jobs');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadJobs();
  }, []);

  const viewJob = async (job) => {
    setSelectedJob(job);
    setLoadingDetails(true);
    try {
      const res = await processingService.getJob(job.job_id);
      setJobDetails(res.data);
    } catch (err) {
      console.error(err);
    } finally {
      setLoadingDetails(false);
    }
  };

  const getStatusBadge = (status) => {
    switch (status) {
      case 'COMPLETED': return <Badge variant="success" className="flex gap-1 w-fit"><CheckCircle2 size={12} /> Completed</Badge>;
      case 'FAILED': return <Badge variant="danger" className="flex gap-1 w-fit"><XCircle size={12} /> Failed</Badge>;
      case 'WARNING': return <Badge variant="warning" className="flex gap-1 w-fit"><Clock size={12} /> Warning</Badge>;
      default: return <Badge className="flex gap-1 w-fit"><Clock size={12} /> {status}</Badge>;
    }
  };

  return (
    <div className="flex flex-col gap-4 max-w-[1600px] mx-auto p-4 w-full h-[calc(100vh-60px)] animate-fade-in-up overflow-hidden">
      {!selectedJob && (
        <div className="flex items-start justify-between flex-shrink-0">
          <div className="flex flex-col gap-1">
            <div className="text-[0.65rem] font-bold text-tertiary tracking-widest uppercase mb-1 flex items-center gap-2">
              <span>Pipelines</span> <span className="text-border-color">›</span> <span>Intelligence Extraction</span>
            </div>
            <h1 className="text-2xl font-bold text-primary font-heading tracking-tight">Extraction Results & Document Inspector</h1>
            <p className="text-sm text-secondary">Real-time multi-engine field extraction, bounding box visual validation, and audit review</p>
          </div>
        </div>
      )}

      {error && <div className="p-3 bg-danger bg-opacity-10 border border-danger rounded-md text-danger flex-shrink-0">{error}</div>}

      <div className="flex gap-4 flex-1 overflow-hidden">
        {!selectedJob && (
        <div className="w-[300px] flex-shrink-0 flex flex-col gap-4">
          <Card className="h-full flex flex-col overflow-hidden shadow-sm border-border-color rounded-md">
            <CardHeader className="bg-bg-primary border-b border-border-color sticky top-0 z-10 px-4 py-3 flex flex-col gap-3">
              <div className="flex justify-between items-center w-full">
                <CardTitle className="text-base flex items-center gap-2">
                  <FileText size={18} className="text-primary-accent" /> Recent Jobs
                </CardTitle>
                <Badge variant="info" className="bg-slate-100 text-slate-600 border-none">{jobs.length} Active</Badge>
              </div>
              <div className="relative w-full">
                <Filter size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-tertiary" />
                <input type="text" placeholder="Filter by hash, form..." className="w-full pl-9 pr-3 py-1.5 text-sm bg-white border border-border-color rounded focus:outline-none focus:border-primary-accent" />
              </div>
              <div className="flex items-center justify-between text-xs font-semibold pt-1 border-b border-border-color pb-1">
                <span className="text-primary-accent border-b-2 border-primary-accent pb-1 cursor-pointer">All ({jobs.length})</span>
                <span className="text-secondary cursor-pointer hover:text-primary">Done ({jobs.filter(j => j.status === 'COMPLETED').length})</span>
                <span className="text-secondary cursor-pointer hover:text-primary">Needs Review (0)</span>
              </div>
            </CardHeader>
            <CardContent className="p-0 overflow-y-auto flex-1 bg-bg-primary">
              {loading ? (
                <div className="p-8 flex justify-center"><Loader text="Loading..." /></div>
              ) : jobs.length === 0 ? (
                <div className="p-8 text-center text-secondary italic">No processing jobs found.</div>
              ) : (
                <div className="flex flex-col">
                  {jobs.map(job => (
                    <div 
                      key={job.job_id} 
                      className={`p-3 border-b border-border-color cursor-pointer transition-colors bg-white hover:bg-slate-50 flex flex-col gap-1.5 ${selectedJob?.job_id === job.job_id ? 'border-l-4 border-l-primary-accent' : 'border-l-4 border-l-transparent'}`}
                      onClick={() => viewJob(job)}
                    >
                      <div className="flex justify-between items-start">
                        <span className="font-bold text-xs text-primary truncate mr-2" title={job.document_id}>
                          Doc:<br/>{job.job_id.substring(0,8)}...
                        </span>
                        {getStatusBadge(job.status)}
                      </div>
                      <div className="text-xs font-semibold text-secondary truncate" title={job.document_id}>
                        {job.document_id}
                      </div>
                      <div className="flex items-center justify-between mt-1">
                        <div className="text-[0.65rem] text-tertiary font-mono">
                          {new Date(job.created_at).toLocaleTimeString()}
                        </div>
                        {job.overall_confidence !== null && (
                          <div className="flex flex-col text-right">
                            <span className="text-[0.65rem] font-bold text-secondary uppercase tracking-wider">Confidence:</span>
                            <span className="text-xs font-bold text-success">{(job.overall_confidence * 100).toFixed(1)}%</span>
                          </div>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </CardContent>
          </Card>
        </div>
        )}

        <div className="flex-1 flex flex-col min-w-0">
          {selectedJob ? (
            (() => {
              const metadata = jobDetails?.ocr_metadata_json ? (typeof jobDetails.ocr_metadata_json === 'string' ? JSON.parse(jobDetails.ocr_metadata_json) : jobDetails.ocr_metadata_json) : null;
              const isOrchestration = jobDetails?.pipeline_mode === 'orchestration' || jobDetails?.pipeline_mode === 'orchestrator' || (metadata && metadata.pipelines && metadata.pipelines.length > 0);
              
              if (isOrchestration) {
                return (
                  <Card className="h-full flex flex-col overflow-hidden border-opacity-50 shadow-sm p-0 rounded-md">
                    <div className="flex flex-col items-center justify-center p-12 text-center h-full bg-[#0f172a] text-white">
                      <GitCompare size={64} className="mb-6 text-primary-accent opacity-80" />
                      <h3 className="text-2xl font-bold mb-3">Orchestration Job</h3>
                      <p className="text-slate-400 mb-8 max-w-md">This job compared multiple pipelines. View the detailed field-level comparison and consensus results on the orchestration canvas.</p>
                      <button 
                        className="btn btn-primary" 
                        style={{ padding: '0.75rem 1.5rem', fontSize: '1rem' }}
                        onClick={() => navigate(`/orchestration/canvas/${jobDetails.job_id}`)}
                      >
                        View Pipeline Results
                      </button>
                    </div>
                  </Card>
                );
              }

              if (jobDetails?.status === 'COMPLETED' || jobDetails?.status === 'WARNING' || jobDetails?.status === 'FAILED') {
                return (
                  <Card className="h-full flex flex-col overflow-hidden border-transparent shadow-none bg-transparent p-0 rounded-none">
                    <ExtractionResultView 
                      activeJob={jobDetails}
                      pipelineSteps={[]}
                      onBack={() => setSelectedJob(null)}
                    />
                  </Card>
                );
              }

              return (
                <Card className="h-full flex flex-col shadow-sm rounded-md overflow-hidden">
                  <CardHeader className="border-b border-border-color bg-bg-secondary">
                    <CardTitle className="flex justify-between items-center">
                      <span>Job Details</span>
                      {getStatusBadge(selectedJob.status)}
                    </CardTitle>
                  </CardHeader>
                  <CardContent className="p-4 overflow-y-auto flex-1 bg-[#0f172a] text-white">
                    {loadingDetails ? (
                      <div className="p-12 flex justify-center"><Loader text="Loading details..." /></div>
                    ) : jobDetails ? (
                      <div className="flex flex-col gap-6">
                        <div className="grid grid-cols-2 gap-4 text-sm bg-slate-800 p-4 rounded-lg">
                          <div><span className="text-slate-400">Job ID:</span> <span className="font-mono">{jobDetails.job_id}</span></div>
                          <div><span className="text-slate-400">Document ID:</span> <span className="font-mono">{jobDetails.document_id}</span></div>
                          <div><span className="text-slate-400">AI Engine:</span> {jobDetails.ai_engine || 'N/A'}</div>
                          <div><span className="text-slate-400">Model:</span> {jobDetails.ai_model || 'N/A'}</div>
                          {jobDetails.error_message && (
                            <div className="col-span-2 text-red-400">
                              <span className="font-bold">Error:</span> {jobDetails.error_message}
                            </div>
                          )}
                        </div>

                        {jobDetails.extracted_json && (
                          <div>
                            <h3 className="text-lg font-semibold mb-2 text-indigo-300">Extracted Data</h3>
                            <pre className="bg-slate-900 p-4 rounded-lg overflow-x-auto text-sm font-mono text-green-400 border border-slate-700">
                              {JSON.stringify(jobDetails.extracted_json, null, 2)}
                            </pre>
                          </div>
                        )}
                        
                        {jobDetails.validation_warnings && jobDetails.validation_warnings.length > 0 && (
                          <div>
                            <h3 className="text-lg font-semibold mb-2 text-yellow-300">Validation Warnings</h3>
                            <ul className="list-disc pl-5 text-yellow-200 text-sm space-y-1">
                              {jobDetails.validation_warnings.map((w, i) => <li key={i}>{w}</li>)}
                            </ul>
                          </div>
                        )}
                      </div>
                    ) : (
                      <div className="p-8 text-center text-slate-400 italic">Could not load details for this job.</div>
                    )}
                  </CardContent>
                </Card>
              );
            })()
          ) : (
            <div className="h-full flex items-center justify-center border-2 border-dashed border-border-color rounded-lg text-secondary">
              <div className="flex flex-col items-center gap-2">
                <FileText size={48} className="opacity-20" />
                <p>Select a job from the list to view its results</p>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

export default ResultsPage;
