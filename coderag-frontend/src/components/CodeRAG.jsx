import React, {
  useState, useEffect, useRef, useCallback, lazy, Suspense
} from 'react';
import {
  Search, GitBranch, ExternalLink, CheckCircle, Activity,
  Layers, ArrowRight, MessageSquare, ShieldCheck, Zap,
  FolderDot, Trash2, BarChart2, Clock, LogOut, Plus,
  ChevronDown, ChevronUp, Copy, Check, AlertTriangle,
  X, Menu, RefreshCw, Info
} from 'lucide-react';
import { supabase } from '../supabaseClient';
import {
  search as apiSearch, fetchUserRepos, deleteRepo as apiDeleteRepo,
  ingestRepo, fetchAnalytics, fetchHistory, fetchGraphData
} from '../services';
import './CodeRAG.css';

/* Lazy-loaded graph to reduce initial bundle */
const NeuralMap = lazy(() => import('./NeuralMap'));

/* ─── Focus trap hook ──────────────────────────────────── */
function useFocusTrap(ref, active) {
  useEffect(() => {
    if (!active || !ref.current) return;
    const el = ref.current;
    const focusable = el.querySelectorAll(
      'button:not([disabled]), [href], input:not([disabled]), select, textarea, [tabindex]:not([tabindex="-1"])'
    );
    const first = focusable[0];
    const last  = focusable[focusable.length - 1];

    const prev = document.activeElement;
    first?.focus();

    function onKey(e) {
      if (e.key !== 'Tab') return;
      if (e.shiftKey ? document.activeElement === first : document.activeElement === last) {
        e.preventDefault();
        (e.shiftKey ? last : first)?.focus();
      }
    }
    el.addEventListener('keydown', onKey);
    return () => {
      el.removeEventListener('keydown', onKey);
      prev?.focus();
    };
  }, [active, ref]);
}

/* ─── Status badge helper ──────────────────────────────── */
function StatusBadge({ status }) {
  const map = {
    ready:    { label: 'Ready',    cls: 'badge-success' },
    indexing: { label: 'Indexing', cls: 'badge-warning' },
    cloning:  { label: 'Cloning',  cls: 'badge-warning' },
    pending:  { label: 'Pending',  cls: 'badge-muted'   },
    failed:   { label: 'Failed',   cls: 'badge-error'   },
    deleting: { label: 'Deleting', cls: 'badge-muted'   },
  };
  const s = map[status] ?? { label: 'Indexed', cls: 'badge-success' };
  return (
    <span className={`status-badge ${s.cls}`} aria-label={`Status: ${s.label}`}>
      {s.label}
    </span>
  );
}

/* ─── Confirmation dialog ──────────────────────────────── */
function ConfirmDialog({ open, title, message, onConfirm, onCancel }) {
  const ref = useRef(null);
  useFocusTrap(ref, open);
  useEffect(() => {
    if (!open) return;
    function onKey(e) { if (e.key === 'Escape') onCancel(); }
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [open, onCancel]);

  if (!open) return null;
  return (
    <div className="modal-backdrop" role="presentation" onClick={onCancel}>
      <div
        ref={ref}
        className="confirm-dialog"
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="confirm-title"
        aria-describedby="confirm-desc"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="confirm-icon" aria-hidden="true">
          <AlertTriangle size={24} />
        </div>
        <h2 id="confirm-title" className="confirm-title">{title}</h2>
        <p id="confirm-desc" className="confirm-desc">{message}</p>
        <div className="confirm-actions">
          <button className="btn-ghost" onClick={onCancel}>Cancel</button>
          <button className="btn-danger" onClick={onConfirm} autoFocus>Delete</button>
        </div>
      </div>
    </div>
  );
}

/* ─── Copy button ──────────────────────────────────────── */
function CopyButton({ text, label = 'Copy', className = '' }) {
  const [copied, setCopied] = useState(false);
  const [status, setStatus] = useState('');

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setStatus('Copied!');
      setTimeout(() => { setCopied(false); setStatus(''); }, 1800);
    } catch {
      setStatus('Copy failed');
      setTimeout(() => setStatus(''), 2000);
    }
  };

  return (
    <button
      type="button"
      className={`copy-btn ${className}`}
      onClick={handleCopy}
      aria-label={copied ? 'Copied to clipboard' : label}
    >
      {copied ? <Check size={14} aria-hidden="true" /> : <Copy size={14} aria-hidden="true" />}
      <span aria-live="polite" className="copy-feedback">{status}</span>
    </button>
  );
}

/* ─── Source card ──────────────────────────────────────── */
function SourceCard({ source, index }) {
  const [expanded, setExpanded] = useState(false);
  const snippetId = `source-snippet-${index}`;
  const language = source.language || detectLang(source.file);
  const hasUrl = source.url && source.url.startsWith('http');

  return (
    <article className="source-card" aria-label={`Source ${index + 1}: ${source.file}`}>
      <div className="source-header">
        <div className="source-file-info">
          <span className="source-file" title={source.file}>
            {source.file}
          </span>
          {source.symbol && source.symbol !== 'block' && (
            <span className="source-symbol">{source.symbol}</span>
          )}
        </div>
        <div className="source-meta-right">
          {source.match_type && (
            <span className="source-match-badge" aria-label={`Match type: ${source.match_type}`}>
              {source.match_type}
              {source.retrieval_rank ? ` #${source.retrieval_rank}` : ''}
            </span>
          )}
          {language && (
            <span className="source-lang-badge" aria-label={`Language: ${language}`}>
              {language}
            </span>
          )}
          {hasUrl && (
            <a
              href={source.url}
              target="_blank"
              rel="noopener noreferrer"
              className="source-link"
              aria-label={`Open ${source.file} on GitHub (opens in new tab)`}
            >
              <ExternalLink size={12} aria-hidden="true" />
              GitHub
            </a>
          )}
          <CopyButton
            text={source.file}
            label={`Copy path for ${source.file}`}
            className="copy-btn-sm"
          />
        </div>
      </div>

      {/* Line range */}
      {(source.start_line || source.end_line) && (
        <div className="source-lines" aria-label={`Lines ${source.start_line}–${source.end_line}`}>
          Lines {source.start_line}–{source.end_line}
        </div>
      )}

      {/* Expand/collapse snippet */}
      <button
        type="button"
        className="source-expand-btn"
        aria-expanded={expanded}
        aria-controls={snippetId}
        onClick={() => setExpanded(!expanded)}
      >
        {expanded
          ? <><ChevronUp size={14} aria-hidden="true" /> Hide snippet</>
          : <><ChevronDown size={14} aria-hidden="true" /> Show snippet</>
        }
      </button>

      {expanded && (
        <div id={snippetId} className="source-snippet-wrapper">
          <div className="source-snippet-toolbar">
            <span className="source-snippet-label" aria-label={`Code snippet in ${language}`}>
              {language || 'code'}
            </span>
            <CopyButton
              text={source.code}
              label={`Copy code from ${source.file}`}
              className="copy-btn-sm"
            />
          </div>
          <pre
            className="source-code"
            aria-label={`Code snippet from ${source.file}`}
            tabIndex={0}
          >
            <code>{source.code}</code>
          </pre>
        </div>
      )}
    </article>
  );
}

function detectLang(filePath) {
  if (!filePath) return '';
  const ext = filePath.split('.').pop()?.toLowerCase();
  const map = { py: 'Python', js: 'JavaScript', jsx: 'JSX', ts: 'TypeScript',
    tsx: 'TSX', java: 'Java', go: 'Go', rs: 'Rust', cpp: 'C++', c: 'C',
    cs: 'C#', rb: 'Ruby', php: 'PHP', md: 'Markdown', json: 'JSON',
    yaml: 'YAML', yml: 'YAML', sh: 'Shell', html: 'HTML', css: 'CSS' };
  return map[ext] || ext.toUpperCase() || '';
}

/* ─── Ingestion modal ──────────────────────────────────── */
function IngestionModal({ open, onClose, onSuccess }) {
  const ref = useRef(null);
  const [url, setUrl]     = useState('');
  const [status, setStatus] = useState({ phase: 'idle', error: '', data: null });
  // phase: idle | loading | success | error

  useFocusTrap(ref, open);
  useEffect(() => {
    if (!open) return;
    function onKey(e) { if (e.key === 'Escape' && status.phase !== 'loading') onClose(); }
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [open, status.phase, onClose]);

  // Reset when opened
  useEffect(() => {
    if (open) { setUrl(''); setStatus({ phase: 'idle', error: '', data: null }); }
  }, [open]);

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!url.trim()) return;
    setStatus({ phase: 'loading', error: '', data: null });
    try {
      const data = await ingestRepo(url.trim());
      setStatus({ phase: 'success', error: '', data });
      onSuccess?.();
    } catch (err) {
      setStatus({ phase: 'error', error: err.message, data: null });
    }
  };

  if (!open) return null;

  return (
    <div className="modal-backdrop" role="presentation">
      <div
        ref={ref}
        className="ingest-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="ingest-modal-title"
        aria-describedby="ingest-modal-desc"
      >
        {/* Header */}
        <div className="modal-header">
          <div className="modal-header-left">
            <GitBranch size={20} className="modal-icon" aria-hidden="true" />
            <h2 id="ingest-modal-title">Index a repository</h2>
          </div>
          <button
            type="button"
            className="modal-close-btn"
            onClick={onClose}
            aria-label="Close dialog"
            disabled={status.phase === 'loading'}
          >
            <X size={18} aria-hidden="true" />
          </button>
        </div>

        <p id="ingest-modal-desc" className="modal-desc">
          Enter a public GitHub repository URL. Cerebro will clone it, generate
          semantic embeddings, and add it to your indexed knowledge base.
        </p>

        {/* Form */}
        {status.phase !== 'success' && (
          <form onSubmit={handleSubmit} className="ingest-form">
            <div className="ingest-field">
              <label htmlFor="ingest-url" className="ingest-label">
                Repository URL
              </label>
              <input
                id="ingest-url"
                type="url"
                className="ingest-input"
                placeholder="https://github.com/owner/repository"
                value={url}
                onChange={(e) => setUrl(e.target.value)}
                required
                disabled={status.phase === 'loading'}
                pattern="https://github\.com/.+/.+"
                aria-describedby={status.phase === 'error' ? 'ingest-error-msg' : undefined}
              />
            </div>
            <button
              type="submit"
              className="btn-primary ingest-submit"
              disabled={status.phase === 'loading' || !url.trim()}
            >
              {status.phase === 'loading'
                ? <><Activity size={16} aria-hidden="true" className="spin" /> Indexing…</>
                : <><Plus size={16} aria-hidden="true" /> Start indexing</>
              }
            </button>
          </form>
        )}

        {/* Loading status */}
        {status.phase === 'loading' && (
          <div className="ingest-progress" role="status" aria-live="polite">
            <div className="ingest-progress-bar" aria-hidden="true">
              <div className="ingest-progress-fill" />
            </div>
            <p className="ingest-progress-text">
              Cloning and indexing your repository. This may take a minute…
            </p>
          </div>
        )}

        {/* Success */}
        {status.phase === 'success' && status.data && (
          <div className="ingest-result ingest-result--success" role="status" aria-live="polite">
            <CheckCircle size={24} aria-hidden="true" className="ingest-result-icon" />
            <h3 className="ingest-result-title">Repository indexed</h3>
            <div className="ingest-metrics">
              {status.data.files_indexed != null && (
                <div className="ingest-metric">
                  <span className="ingest-metric-value">{status.data.files_indexed}</span>
                  <span className="ingest-metric-label">Files indexed</span>
                </div>
              )}
              {status.data.chunks_generated != null && (
                <div className="ingest-metric">
                  <span className="ingest-metric-value">{status.data.chunks_generated}</span>
                  <span className="ingest-metric-label">Chunks generated</span>
                </div>
              )}
              {status.data.indexed_count != null && (
                <div className="ingest-metric">
                  <span className="ingest-metric-value">{status.data.indexed_count}</span>
                  <span className="ingest-metric-label">Snippets indexed</span>
                </div>
              )}
            </div>
            <button type="button" className="btn-primary" onClick={onClose} style={{ marginTop: '1rem', width: '100%' }}>
              Done
            </button>
          </div>
        )}

        {/* Error */}
        {status.phase === 'error' && (
          <div
            id="ingest-error-msg"
            className="ingest-result ingest-result--error"
            role="alert"
            aria-live="assertive"
          >
            <AlertTriangle size={20} aria-hidden="true" />
            <p>{status.error}</p>
          </div>
        )}
      </div>
    </div>
  );
}

/* ─── Search result skeleton ───────────────────────────── */
function SearchSkeleton() {
  return (
    <div className="search-skeleton" aria-label="Loading results" aria-busy="true">
      <div className="skeleton-block skeleton-h3" />
      <div className="skeleton-block skeleton-p" />
      <div className="skeleton-block skeleton-p-sm" />
      <div className="skeleton-block skeleton-p-sm" style={{ width: '60%' }} />
    </div>
  );
}

/* ─── Main workspace ───────────────────────────────────── */
export default function Cerebro({ user }) {
  /* Navigation */
  const [view, setView]             = useState('search');
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const sidebarRef = useRef(null);
  useFocusTrap(sidebarRef, sidebarOpen && window.innerWidth <= 768);

  /* Repositories */
  const [userRepos, setUserRepos]   = useState([]);
  const [repoLoading, setRepoLoading] = useState(false);
  const [repoFilter, setRepoFilter] = useState('');

  /* Search */
  const [query, setQuery]           = useState('');
  const [loading, setLoading]       = useState(false);
  const [results, setResults]       = useState(null);
  const [searchError, setSearchError] = useState(null); // { message, status, retryAfter? }
  const [chatContext, setChatContext] = useState([]);
  const resultsRef = useRef(null);

  /* Delete confirmation */
  const [deleteTarget, setDeleteTarget] = useState(null);  // repo name

  /* Ingestion */
  const [showIngestModal, setShowIngestModal] = useState(false);

  /* Analytics + History */
  const [analytics, setAnalytics]   = useState(null);
  const [analyticsError, setAnalyticsError] = useState(null);
  const [history, setHistory]       = useState([]);
  const [historyError, setHistoryError] = useState(null);

  /* ── Load repos on mount ─────────────────────────────── */
  const loadRepos = useCallback(async () => {
    setRepoLoading(true);
    try {
      const data = await fetchUserRepos();
      setUserRepos(data.repositories || data.repos?.map(name => ({ repository_name: name })) || []);
    } catch {
      // Silently handled — empty state shown
    } finally {
      setRepoLoading(false);
    }
  }, []);

  useEffect(() => { loadRepos(); }, [loadRepos]);

  /* ── Load dashboard data when view switches ──────────── */
  useEffect(() => {
    if (view !== 'dashboard') return;
    (async () => {
      setAnalyticsError(null);
      setHistoryError(null);
      try {
        const [a, h] = await Promise.all([fetchAnalytics(), fetchHistory()]);
        setAnalytics(a);
        setHistory(h);
      } catch (err) {
        setAnalyticsError(err.message);
        setHistoryError(err.message);
      }
    })();
  }, [view]);

  /* ── Sidebar: close on Escape, focus restore ─────────── */
  useEffect(() => {
    if (!sidebarOpen) return;
    function onKey(e) { if (e.key === 'Escape') setSidebarOpen(false); }
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [sidebarOpen]);

  /* ── Search ─────────────────────────────────────────── */
  const performSearch = async (q) => {
    const trimmed = q.trim();
    if (!trimmed) return;
    setLoading(true);
    setSearchError(null);
    setResults(null);   // clear stale results before new request

    try {
      const data = await apiSearch({
        query: trimmed,
        repoFilter: repoFilter || undefined,
        history: chatContext,
        topK: 5,
      });
      setResults(data);
      setChatContext(prev => [
        ...prev,
        { role: 'user', content: trimmed },
        { role: 'assistant', content: data.answer },
      ]);
      // Scroll to results
      setTimeout(() => resultsRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' }), 100);
    } catch (err) {
      setSearchError({ message: err.message, status: err.status, retryAfter: err.retryAfter });
    } finally {
      setLoading(false);
    }
  };

  const handleSearchSubmit = (e) => {
    e.preventDefault();
    performSearch(query);
  };

  /* ── Delete repo ──────────────────────────────────────── */
  const confirmDelete = (repoName) => setDeleteTarget(repoName);
  const handleDeleteConfirm = async () => {
    if (!deleteTarget) return;
    const name = deleteTarget;
    setDeleteTarget(null);
    try {
      await apiDeleteRepo(name);
      setUserRepos(prev => prev.filter(r => (r.repository_name ?? r) !== name));
      if (repoFilter === name) setRepoFilter('');
    } catch {
      // Error is swallowed — the repo list will be refreshed on next load
    }
  };

  /* ── Nav tabs ───────────────────────────────────────── */
  const NAV_TABS = [
    { id: 'search',    label: 'Search',       icon: <Search size={16} aria-hidden="true" /> },
    { id: 'repos',     label: 'Repositories', icon: <FolderDot size={16} aria-hidden="true" /> },
    { id: 'graph',     label: 'Knowledge map',icon: <Layers size={16} aria-hidden="true" /> },
    { id: 'dashboard', label: 'Analytics',    icon: <BarChart2 size={16} aria-hidden="true" /> },
  ];

  const handleTabKeyDown = (e, tabId, idx) => {
    let next = idx;
    if (e.key === 'ArrowRight') next = (idx + 1) % NAV_TABS.length;
    else if (e.key === 'ArrowLeft') next = (idx - 1 + NAV_TABS.length) % NAV_TABS.length;
    else return;
    e.preventDefault();
    const target = document.getElementById(`tab-${NAV_TABS[next].id}`);
    target?.focus();
    setView(NAV_TABS[next].id);
  };

  /* ─────────────────────────────────────────────────────── */
  return (
    <>
      <div className="workspace">
        {/* ── Skip link target ─── */}
        <a href="#main-content" className="skip-link">Skip to main content</a>

        {/* ── Header ───────────────────────────────────── */}
        <header className="ws-header">
          <div className="ws-header-left">
            <button
              type="button"
              className="sidebar-toggle"
              aria-label={sidebarOpen ? 'Close navigation' : 'Open navigation'}
              aria-expanded={sidebarOpen}
              aria-controls="workspace-nav"
              onClick={() => setSidebarOpen(!sidebarOpen)}
            >
              <Menu size={20} aria-hidden="true" />
            </button>
            <div className="ws-brand" aria-label="Cerebro">
              <ShieldCheck size={18} className="ws-brand-icon" aria-hidden="true" />
              <span className="ws-brand-name">CEREBRO</span>
            </div>
          </div>

          <nav
            id="workspace-nav"
            className={`ws-nav ${sidebarOpen ? 'ws-nav--open' : ''}`}
            aria-label="Workspace navigation"
            ref={sidebarRef}
            role="tablist"
            aria-orientation="horizontal"
          >
            {/* Mobile: backdrop */}
            {sidebarOpen && (
              <div
                className="nav-backdrop"
                aria-hidden="true"
                onClick={() => setSidebarOpen(false)}
              />
            )}
            <div className="ws-nav-inner">
              {NAV_TABS.map((tab, idx) => (
                <button
                  key={tab.id}
                  id={`tab-${tab.id}`}
                  type="button"
                  role="tab"
                  className={`ws-nav-tab ${view === tab.id ? 'ws-nav-tab--active' : ''}`}
                  aria-selected={view === tab.id}
                  aria-controls={`panel-${tab.id}`}
                  tabIndex={view === tab.id ? 0 : -1}
                  onClick={() => { setView(tab.id); setSidebarOpen(false); }}
                  onKeyDown={(e) => handleTabKeyDown(e, tab.id, idx)}
                >
                  {tab.icon}
                  <span>{tab.label}</span>
                </button>
              ))}
            </div>
          </nav>

          <div className="ws-header-right">
            <div className="ws-user" aria-label={`Signed in as ${user.email}`}>
              <div className="ws-avatar" aria-hidden="true">
                {user.email?.[0]?.toUpperCase() ?? 'U'}
              </div>
              <span className="ws-email">{user.email}</span>
            </div>
            <button
              type="button"
              className="ws-ingest-btn"
              onClick={() => setShowIngestModal(true)}
              aria-label="Index a new repository"
            >
              <Plus size={16} aria-hidden="true" />
              <span>Index repo</span>
            </button>
            <button
              type="button"
              className="ws-signout-btn"
              onClick={() => supabase.auth.signOut()}
              aria-label="Sign out of Cerebro"
            >
              <LogOut size={16} aria-hidden="true" />
              <span>Sign out</span>
            </button>
          </div>
        </header>

        {/* ── Main content ─────────────────────────────── */}
        <main id="main-content" className="ws-main">

          {/* ────────── SEARCH VIEW ───────────────────── */}
          <div
            id="panel-search"
            role="tabpanel"
            aria-labelledby="tab-search"
            hidden={view !== 'search'}
            className="ws-panel"
          >
            {/* Search form */}
            <section className="search-section" aria-label="Code search">
              <form onSubmit={handleSearchSubmit} className="search-form" noValidate>
                <div className="search-bar-wrapper">
                  <label htmlFor="search-input" className="sr-only">
                    Search your codebase
                  </label>
                  <Search size={18} className="search-icon" aria-hidden="true" />
                  <input
                    id="search-input"
                    type="search"
                    className="search-input"
                    placeholder="Ask a question about your codebase…"
                    value={query}
                    onChange={(e) => setQuery(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter' && !e.shiftKey) {
                        e.preventDefault();
                        performSearch(query);
                      }
                    }}
                    autoComplete="off"
                    aria-label="Search query"
                  />
                  <button
                    type="submit"
                    className="search-submit"
                    disabled={loading || !query.trim()}
                    aria-label={loading ? 'Searching…' : 'Search'}
                  >
                    {loading
                      ? <Activity size={18} className="spin" aria-hidden="true" />
                      : <ArrowRight size={18} aria-hidden="true" />
                    }
                  </button>
                </div>

                {/* Repo filter */}
                <div className="repo-filter-bar">
                  <label htmlFor="repo-filter" className="repo-filter-label">
                    <FolderDot size={14} aria-hidden="true" />
                    Repository scope
                  </label>
                  <select
                    id="repo-filter"
                    className="repo-filter-select"
                    value={repoFilter}
                    onChange={(e) => setRepoFilter(e.target.value)}
                    aria-describedby="repo-filter-hint"
                  >
                    <option value="">All repositories</option>
                    {userRepos.map((r) => {
                      const name = r.repository_name ?? r;
                      return <option key={name} value={name}>{name}</option>;
                    })}
                  </select>
                  <span id="repo-filter-hint" className="sr-only">
                    Leave blank to search across all indexed repositories
                  </span>
                </div>
              </form>
            </section>

            {/* Results region */}
            <section
              aria-live="polite"
              aria-atomic="false"
              aria-label="Search results"
              className="results-region"
            >
              {/* Loading */}
              {loading && <SearchSkeleton />}

              {/* Error */}
              {!loading && searchError && (
                <div
                  className="error-card"
                  role="alert"
                  aria-live="assertive"
                >
                  <AlertTriangle size={18} aria-hidden="true" />
                  <div>
                    <p className="error-card-msg">{searchError.message}</p>
                    {searchError.retryAfter && (
                      <p className="error-card-hint">
                        Retry after {searchError.retryAfter}s
                      </p>
                    )}
                    {searchError.status === 401 && (
                      <button
                        type="button"
                        className="btn-ghost error-action"
                        onClick={() => supabase.auth.signOut()}
                      >
                        Sign in again
                      </button>
                    )}
                  </div>
                </div>
              )}

              {/* Results */}
              {!loading && results && (
                <div className="results-container" ref={resultsRef}>
                  {/* Answer card */}
                  <article className="answer-card" aria-label="Grounded answer">
                    <div className="answer-card-header">
                      <h2 className="answer-card-title">Answer</h2>
                      <div className="answer-badges">
                        <span className="badge-grounded" aria-label="Grounded by retrieved sources">
                          <ShieldCheck size={12} aria-hidden="true" />
                          Grounded
                        </span>
                        {results.metadata?.retrievalStrategy && (
                          <span className="badge-strategy">
                            {results.metadata.retrievalStrategy}
                          </span>
                        )}
                      </div>
                      <CopyButton text={results.answer} label="Copy answer to clipboard" />
                    </div>

                    {/* Summary */}
                    {results.summary && (
                      <p className="answer-summary">{results.summary}</p>
                    )}

                    {/* Main answer */}
                    <div className="answer-body">{results.answer}</div>

                    {/* Limitations */}
                    {results.limitations?.length > 0 && (
                      <div className="answer-limitations">
                        <h3 className="answer-limitations-title">
                          <Info size={14} aria-hidden="true" />
                          Context limitations
                        </h3>
                        <ul className="answer-limitations-list">
                          {results.limitations.map((lim, i) => (
                            <li key={i}>{lim}</li>
                          ))}
                        </ul>
                      </div>
                    )}

                    {/* Timing */}
                    {results.metadata && (
                      <div className="answer-timing" aria-label="Retrieval timing">
                        {results.metadata.retrievalTimeMs != null && (
                          <span className="timing-chip">
                            <Search size={12} aria-hidden="true" />
                            <span aria-label={`Retrieval: ${results.metadata.retrievalTimeMs} milliseconds`}>
                              {results.metadata.retrievalTimeMs}ms retrieval
                            </span>
                          </span>
                        )}
                        {results.metadata.generationTimeMs != null && (
                          <span className="timing-chip">
                            <Zap size={12} aria-hidden="true" />
                            <span aria-label={`AI generation: ${results.metadata.generationTimeMs} milliseconds`}>
                              {results.metadata.generationTimeMs}ms generation
                            </span>
                          </span>
                        )}
                        {results.metadata.totalTimeMs != null && (
                          <span className="timing-chip">
                            <Clock size={12} aria-hidden="true" />
                            <span aria-label={`Total: ${results.metadata.totalTimeMs} milliseconds`}>
                              {results.metadata.totalTimeMs}ms total
                            </span>
                          </span>
                        )}
                        {results.metadata.sourcesRetrieved != null && (
                          <span className="timing-chip">
                            <Layers size={12} aria-hidden="true" />
                            <span aria-label={`${results.metadata.sourcesRetrieved} sources retrieved, ${results.metadata.sourcesCited ?? 0} cited`}>
                              {results.metadata.sourcesRetrieved} retrieved
                              {' / '}
                              {results.metadata.sourcesCited ?? 0} cited
                            </span>
                          </span>
                        )}
                      </div>
                    )}

                    {/* Follow-ups */}
                    {results.follow_ups?.length > 0 && (
                      <div className="follow-ups" aria-label="Suggested follow-up questions">
                        <h3 className="follow-ups-title">
                          <MessageSquare size={14} aria-hidden="true" />
                          Follow-up questions
                        </h3>
                        <div className="follow-ups-chips">
                          {results.follow_ups.slice(0, 3).map((q, i) => (
                            <button
                              key={i}
                              type="button"
                              className="follow-up-chip"
                              aria-label={`Fill question: ${q}`}
                              title="Click to fill this question into the search box"
                              onClick={() => setQuery(q)}
                            >
                              {q}
                            </button>
                          ))}
                        </div>
                        <p className="follow-ups-hint" id="follow-up-hint">
                          <Info size={12} aria-hidden="true" />
                          Clicking fills the question — press Enter or the search button to submit.
                        </p>
                      </div>
                    )}
                  </article>

                  {/* Sources */}
                  {results.sources?.length > 0 ? (
                    <section className="sources-section" aria-labelledby="sources-heading">
                      <h2 id="sources-heading" className="sources-heading">
                        <Layers size={18} aria-hidden="true" />
                        Source citations
                        <span className="sources-count" aria-label={`${results.sources.length} sources`}>
                          {results.sources.length}
                        </span>
                      </h2>
                      <div className="sources-list">
                        {results.sources.map((src, idx) => (
                          <SourceCard key={idx} source={src} index={idx} />
                        ))}
                      </div>
                    </section>
                  ) : (
                    /* No sources — answer still shown above */
                    results.answer && (
                      <div className="no-sources-note" role="note">
                        <Info size={14} aria-hidden="true" />
                        No direct source citations were matched for this answer.
                      </div>
                    )
                  )}
                </div>
              )}

              {/* Idle state */}
              {!loading && !results && !searchError && (
                <div className="search-idle" aria-label="Search idle">
                  <Search size={32} className="search-idle-icon" aria-hidden="true" />
                  <h2 className="search-idle-title">Ask your codebase a question</h2>
                  <p className="search-idle-desc">
                    Cerebro retrieves grounded answers with verified source citations from
                    your indexed repositories.
                    {userRepos.length === 0 && (
                      <> Index a repository first using the <strong>Index repo</strong> button.</>
                    )}
                  </p>
                </div>
              )}
            </section>
          </div>

          {/* ────────── REPOSITORIES VIEW ─────────────── */}
          <div
            id="panel-repos"
            role="tabpanel"
            aria-labelledby="tab-repos"
            hidden={view !== 'repos'}
            className="ws-panel"
          >
            <div className="panel-header">
              <div>
                <h1 className="panel-title">Repositories</h1>
                <p className="panel-subtitle">
                  Repositories indexed to your account. Each has a user-isolated embedding index.
                </p>
              </div>
              <div className="panel-header-actions">
                <button
                  type="button"
                  className="btn-ghost"
                  onClick={loadRepos}
                  aria-label="Refresh repository list"
                >
                  <RefreshCw size={15} aria-hidden="true" />
                  Refresh
                </button>
                <button
                  type="button"
                  className="btn-primary"
                  onClick={() => setShowIngestModal(true)}
                  aria-label="Index a new repository"
                >
                  <Plus size={16} aria-hidden="true" />
                  Index repository
                </button>
              </div>
            </div>

            {repoLoading ? (
              <div className="loading-panel" role="status" aria-live="polite">
                <div className="loading-spinner" aria-hidden="true" />
                <span>Loading repositories…</span>
              </div>
            ) : userRepos.length === 0 ? (
              <div className="empty-state">
                <FolderDot size={36} className="empty-icon" aria-hidden="true" />
                <h2 className="empty-title">No repositories indexed</h2>
                <p className="empty-desc">
                  Connect a public GitHub repository to start searching your codebase.
                </p>
                <button
                  type="button"
                  className="btn-primary"
                  onClick={() => setShowIngestModal(true)}
                >
                  <Plus size={16} aria-hidden="true" />
                  Index your first repository
                </button>
              </div>
            ) : (
              <ul className="repo-grid" aria-label="Indexed repositories">
                {userRepos.map((repo) => {
                  const name    = repo.repository_name ?? repo;
                  const version = repo.active_index_version;
                  const status  = repo.status ?? 'ready';
                  return (
                    <li key={name} className="repo-card">
                      <div className="repo-card-body">
                        <div className="repo-card-header">
                          <FolderDot size={20} aria-hidden="true" className="repo-icon" />
                          <h2 className="repo-name">{name}</h2>
                        </div>
                        <div className="repo-meta">
                          <StatusBadge status={status} />
                          {version && (
                            <span className="repo-version" aria-label={`Index version ${version}`}>
                              v{version}
                            </span>
                          )}
                        </div>
                      </div>
                      <div className="repo-card-footer">
                        <button
                          type="button"
                          className="repo-search-btn"
                          onClick={() => { setRepoFilter(name); setView('search'); }}
                          aria-label={`Search in ${name}`}
                        >
                          <Search size={14} aria-hidden="true" />
                          Search
                        </button>
                        <button
                          type="button"
                          className="repo-delete-btn"
                          onClick={() => confirmDelete(name)}
                          aria-label={`Delete repository ${name}`}
                        >
                          <Trash2 size={14} aria-hidden="true" />
                          Delete
                        </button>
                      </div>
                    </li>
                  );
                })}
              </ul>
            )}
          </div>

          {/* ────────── KNOWLEDGE MAP VIEW ────────────── */}
          <div
            id="panel-graph"
            role="tabpanel"
            aria-labelledby="tab-graph"
            hidden={view !== 'graph'}
            className="ws-panel ws-panel--graph"
          >
            <Suspense fallback={
              <div className="loading-panel" role="status" aria-live="polite">
                <div className="loading-spinner" aria-hidden="true" />
                <span>Loading knowledge map…</span>
              </div>
            }>
              {view === 'graph' && <NeuralMap userId={user.id} />}
            </Suspense>
          </div>

          {/* ────────── ANALYTICS VIEW ────────────────── */}
          <div
            id="panel-dashboard"
            role="tabpanel"
            aria-labelledby="tab-dashboard"
            hidden={view !== 'dashboard'}
            className="ws-panel"
          >
            <div className="panel-header">
              <div>
                <h1 className="panel-title">Analytics</h1>
                <p className="panel-subtitle">Search activity scoped to your account.</p>
              </div>
            </div>

            {analyticsError ? (
              <div className="error-card" role="alert">
                <AlertTriangle size={16} aria-hidden="true" />
                <p>{analyticsError}</p>
              </div>
            ) : analytics ? (
              <>
                <dl className="stats-grid">
                  <div className="stat-card">
                    <dt className="stat-label">Total searches</dt>
                    <dd className="stat-value">{analytics.total_searches ?? 0}</dd>
                  </div>
                  <div className="stat-card">
                    <dt className="stat-label">Avg latency</dt>
                    <dd className="stat-value">
                      {analytics.avg_latency_ms ?? '—'}
                      <span className="stat-unit">ms</span>
                    </dd>
                  </div>
                  <div className="stat-card">
                    <dt className="stat-label">Avg confidence</dt>
                    <dd className="stat-value">
                      {analytics.avg_confidence ?? '—'}
                      {analytics.avg_confidence != null && <span className="stat-unit">%</span>}
                    </dd>
                  </div>
                </dl>

                {history.length > 0 && (
                  <section className="history-section" aria-labelledby="history-heading">
                    <h2 id="history-heading" className="history-title">
                      <Clock size={16} aria-hidden="true" />
                      Recent queries
                    </h2>
                    <ul className="history-list" aria-label="Search history">
                      {history.slice(0, 20).map((item) => (
                        <li key={item.id} className="history-item">
                          <button
                            type="button"
                            className="history-query-btn"
                            onClick={() => { setQuery(item.query); setView('search'); }}
                            aria-label={`Repeat search: ${item.query}`}
                          >
                            {item.query}
                          </button>
                          <time
                            className="history-time"
                            dateTime={item.timestamp}
                            aria-label={new Date(item.timestamp).toLocaleString()}
                          >
                            {new Date(item.timestamp).toLocaleTimeString()}
                          </time>
                        </li>
                      ))}
                    </ul>
                  </section>
                )}

                {history.length === 0 && !historyError && (
                  <div className="empty-state" style={{ marginTop: '2rem' }}>
                    <Clock size={28} className="empty-icon" aria-hidden="true" />
                    <h2 className="empty-title">No search history yet</h2>
                    <p className="empty-desc">Your recent queries will appear here.</p>
                  </div>
                )}
              </>
            ) : (
              <div className="loading-panel" role="status" aria-live="polite">
                <div className="loading-spinner" aria-hidden="true" />
                <span>Loading analytics…</span>
              </div>
            )}
          </div>
        </main>
      </div>

      {/* ── Modals / Dialogs ─────────────────────────── */}
      <IngestionModal
        open={showIngestModal}
        onClose={() => setShowIngestModal(false)}
        onSuccess={loadRepos}
      />

      <ConfirmDialog
        open={!!deleteTarget}
        title="Delete repository?"
        message={`This will permanently remove "${deleteTarget}" and all its indexed snippets from your account. This cannot be undone.`}
        onConfirm={handleDeleteConfirm}
        onCancel={() => setDeleteTarget(null)}
      />
    </>
  );
}
