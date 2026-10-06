/**
 * DOCINT — Help & Support page.
 * Static FAQ plus a live system-status strip reused from /workers/health.
 */
import React, { useState, useEffect } from 'react';
import { LifeBuoy, Mail, BookOpen, ChevronDown, Database, Cpu } from 'lucide-react';
import { pipelinesService } from '../services/pipelines';
import { Card, CardContent, CardHeader, CardTitle } from '../components/ui/Card';
import { Badge } from '../components/ui/Badge';

const FAQS = [
  {
    q: 'What is the difference between the OCR page and Orchestration?',
    a: 'The OCR page runs exactly one pipeline you choose on a document. Orchestration runs several pipelines on the same document at once and reconciles their results field-by-field using the consensus engine, flagging anything the pipelines disagree on.',
  },
  {
    q: 'Why is a field flagged for review?',
    a: 'A field is flagged when the selected pipelines disagree on its value, or when confidence is too low to resolve automatically. Flagged fields still get a best-guess consensus value, but it is worth a manual check.',
  },
  {
    q: 'How do I share a pre-filled upload link with another team?',
    a: 'Use the Generate URL page: pick a department and (optionally) a template, generate a link, and copy it. Opening that link pre-fills the OCR page’s department/template selectors.',
  },
  {
    q: 'Where do I manage departments and templates?',
    a: 'The Manage page handles both: create/deactivate departments, and create/activate/deactivate/delete templates per department.',
  },
  {
    q: 'Does DOCINT require login?',
    a: 'No — authentication is intentionally out of scope for the current deployment. This is an internal tool; treat generated links as convenience links, not access control.',
  },
];

const FaqItem = ({ q, a, idx }) => {
  const [open, setOpen] = useState(false);
  return (
    <div className="border-b border-border-color border-opacity-50 animate-fade-in-up" style={{ animationDelay: `${idx * 0.04}s` }}>
      <button
        className="w-full flex items-center justify-between py-4 text-left"
        style={{ background: 'transparent', border: 'none', cursor: 'pointer' }}
        onClick={() => setOpen((o) => !o)}
      >
        <span className="font-medium text-primary">{q}</span>
        <ChevronDown size={18} className="text-secondary transition-transform" style={{ transform: open ? 'rotate(180deg)' : 'none' }} />
      </button>
      {open && <p className="text-secondary text-sm pb-4 pr-6">{a}</p>}
    </div>
  );
};

const HelpSupportPage = () => {
  const [health, setHealth] = useState(null);

  useEffect(() => {
    pipelinesService.health().then((res) => setHealth(res.data)).catch(() => setHealth(null));
  }, []);

  return (
    <div className="flex flex-col gap-6 max-w-4xl mx-auto p-4 w-full">
      <div className="flex flex-col text-center mt-2 mb-2">
        <h2 className="text-3xl font-bold text-primary font-heading mb-2 flex items-center justify-center gap-3">
          <LifeBuoy className="text-primary-accent" /> Help &amp; Support
        </h2>
        <p className="text-secondary max-w-lg mx-auto">Answers to common questions, plus a quick look at system status.</p>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <Card className="animate-fade-in-up"><CardContent>
          <div className="flex items-center gap-3">
            <Database size={20} className={health?.redis_reachable ? 'text-success' : 'text-danger'} />
            <div>
              <div className="text-xs text-tertiary uppercase tracking-wider">Job Queue</div>
              <div className="font-semibold text-primary">{health ? (health.redis_reachable ? 'Connected' : 'Unreachable') : 'Checking...'}</div>
            </div>
          </div>
        </CardContent></Card>
        <Card className="animate-fade-in-up" style={{ animationDelay: '0.05s' }}><CardContent>
          <div className="flex items-center gap-3">
            <Cpu size={20} className={health?.gpu?.cuda_available ? 'text-success' : 'text-secondary'} />
            <div>
              <div className="text-xs text-tertiary uppercase tracking-wider">GPU</div>
              <div className="font-semibold text-primary">{health ? (health.gpu.cuda_available ? 'Available' : 'Not detected') : 'Checking...'}</div>
            </div>
          </div>
        </CardContent></Card>
      </div>

      <Card className="animate-fade-in-up" style={{ animationDelay: '0.1s' }}>
        <CardHeader><CardTitle className="flex items-center gap-2"><BookOpen size={18} className="text-primary-accent" /> Frequently Asked Questions</CardTitle></CardHeader>
        <CardContent className="pt-0">
          {FAQS.map((item, idx) => <FaqItem key={item.q} {...item} idx={idx} />)}
        </CardContent>
      </Card>

      <Card className="animate-fade-in-up" style={{ animationDelay: '0.3s' }}>
        <CardContent>
          <div className="flex items-center gap-3">
            <Mail size={20} className="text-primary-accent" />
            <div>
              <div className="font-medium text-primary">Need something else?</div>
              <div className="text-sm text-secondary">Reach out to the DOCINT maintainers directly for anything not covered here.</div>
            </div>
          </div>
        </CardContent>
      </Card>
    </div>
  );
};

export default HelpSupportPage;
