import React, { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { Search, Filter, Eye, RefreshCw } from 'lucide-react';
import { documentsService } from '../services/documents';
import { Card, CardContent, CardHeader, CardTitle } from '../components/ui/Card';
import { Badge } from '../components/ui/Badge';
import { Loader } from '../components/ui/Loader';

const StatusBadge = ({ status }) => {
  switch (status) {
    case 'COMPLETED': return <Badge variant="success">Completed</Badge>;
    case 'FAILED': return <Badge variant="danger">Failed</Badge>;
    case 'PROCESSING': return <Badge variant="warning">Processing</Badge>;
    case 'QUEUED': return <Badge variant="info">Queued</Badge>;
    case 'PENDING': return <Badge variant="default">Pending</Badge>;
    default: return <Badge variant="default">{status}</Badge>;
  }
};

const Documents = () => {
  const [documents, setDocuments] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const navigate = useNavigate();

  const fetchDocuments = async () => {
    setLoading(true);
    try {
      const response = await documentsService.list();
      setDocuments(response.data);
      setError(null);
    } catch (err) {
      setError(err.message || 'Failed to fetch documents');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchDocuments();
  }, []);

  return (
    <div className="flex flex-col gap-6 h-full">
      <div className="flex items-center justify-between">
        <h2 className="text-2xl font-bold text-primary font-heading">Document Archive</h2>
        <div className="flex gap-3">
          <button className="btn btn-secondary" onClick={fetchDocuments}>
            <RefreshCw size={16} /> Refresh
          </button>
          <button className="btn btn-primary" onClick={() => navigate('/upload')}>
            Process New
          </button>
        </div>
      </div>

      <Card className="animate-fade-in flex-1 overflow-hidden flex flex-col">
        <CardHeader className="flex justify-between items-center" style={{paddingBottom: '1rem'}}>
          <div className="search-container" style={{maxWidth: '300px', margin: 0}}>
            <Search className="search-icon" size={16} />
            <input type="text" placeholder="Search filename..." className="search-input" />
          </div>
          <button className="btn btn-secondary">
            <Filter size={16} /> Filter
          </button>
        </CardHeader>
        
        <CardContent className="flex-1 overflow-auto p-0" style={{padding: 0}}>
          {loading ? (
            <div className="h-64 flex items-center justify-center">
              <Loader text="Loading documents..." />
            </div>
          ) : error ? (
            <div className="text-danger p-6 text-center">{error}</div>
          ) : documents.length === 0 ? (
            <div className="h-64 flex flex-col items-center justify-center text-secondary">
              <div className="text-6xl mb-4">📭</div>
              <p>No documents found.</p>
            </div>
          ) : (
            <table className="w-full text-left" style={{width: '100%', borderCollapse: 'collapse'}}>
              <thead style={{background: 'rgba(255,255,255,0.02)', borderBottom: '1px solid var(--border-color)'}}>
                <tr>
                  <th className="p-4 font-medium text-secondary text-sm">Filename</th>
                  <th className="p-4 font-medium text-secondary text-sm">Type</th>
                  <th className="p-4 font-medium text-secondary text-sm">Status</th>
                  <th className="p-4 font-medium text-secondary text-sm">Uploaded At</th>
                  <th className="p-4 font-medium text-secondary text-sm text-right">Actions</th>
                </tr>
              </thead>
              <tbody>
                {documents.map((doc) => (
                  <tr key={doc.id} style={{borderBottom: '1px solid rgba(255,255,255,0.03)', transition: 'background 0.2s'}} className="hover:bg-opacity-5 hover:bg-white">
                    <td className="p-4">
                      <div className="font-medium text-primary">{doc.original_filename}</div>
                      <div className="text-xs text-tertiary mt-1">{doc.id.substring(0, 8)}...</div>
                    </td>
                    <td className="p-4 text-sm">{doc.document_type_code}</td>
                    <td className="p-4"><StatusBadge status={doc.status} /></td>
                    <td className="p-4 text-sm text-secondary">
                      {new Date(doc.created_at).toLocaleString()}
                    </td>
                    <td className="p-4 text-right">
                      <button 
                        className="btn btn-secondary" 
                        style={{padding: '0.4rem 0.6rem'}}
                        onClick={() => navigate(`/documents/${doc.id}`)}
                      >
                        <Eye size={16} /> View
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

export default Documents;
