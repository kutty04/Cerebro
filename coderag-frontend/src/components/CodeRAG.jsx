import React, { useState, useEffect } from 'react';
import { 
  Search, 
  Terminal, 
  Sparkles, 
  BookOpen, 
  ExternalLink, 
  GitBranch, 
  CheckCircle, 
  Activity, 
  Layers,
  ArrowRight,
  BrainCircuit,
  MessageSquare,
  ShieldCheck,
  Zap,
  FolderDot,
  Trash2,
  Cpu,
  BarChart2,
  Clock,
  LogOut
} from 'lucide-react';
import { supabase } from '../supabaseClient';
import { apiFetch } from '../apiClient';
import NeuralMap from './NeuralMap';
import './CodeRAG.css';

export default function Cerebro({ user }) {
  const [query, setQuery] = useState('');
  const [repoFilter, setRepoFilter] = useState('');
  const [loading, setLoading] = useState(false);
  const [results, setResults] = useState(null);
  const [error, setError] = useState('');
  const [view, setView] = useState('search');
  const [analytics, setAnalytics] = useState(null);
  const [history, setHistory] = useState([]);
  const [isScanning, setIsScanning] = useState(false);
  const [chatContext, setChatContext] = useState([]);
  const [showIngestModal, setShowIngestModal] = useState(false);
  const [ingestUrl, setIngestUrl] = useState('');
  const [ingestStatus, setIngestStatus] = useState({ loading: false, error: '', success: '', logs: [] });

  const addLog = (msg) => {
    setIngestStatus(prev => ({ ...prev, logs: [...prev.logs, msg] }));
  };

  const handleIngest = async (e) => {
    e.preventDefault();
    if (!ingestUrl.trim()) return;

    setIngestStatus({ loading: true, error: '', success: '', logs: ['📡 Connecting to Neural Core...'] });
    
    try {
      setTimeout(() => addLog('🧬 Initializing repository clone...'), 800);
      setTimeout(() => addLog('📁 Scanning file structure...'), 1800);
      setTimeout(() => addLog('🧠 Generating semantic embeddings...'), 3500);

      const response = await apiFetch('/ingest', {
        method: 'POST',
        body: JSON.stringify({ 
          repo_url: ingestUrl,
        }),
      });

      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || 'Ingestion failed');
      
      setIngestStatus({ 
        loading: false, 
        error: '', 
        success: `Successfully connected ${data.indexed_count} nodes to Cerebro!` 
      });
      setIngestUrl('');
      setTimeout(() => setShowIngestModal(false), 3000);
      fetchUserRepos();
    } catch (err) {
      setIngestStatus({ 
        loading: false, 
        error: err.message || 'Failed to ingest repository.', 
        success: '', 
        logs: [] 
      });
    }
  };

  const fetchDashboardData = async () => {
    try {
      const [res1, res2] = await Promise.all([
        apiFetch('/analytics'),
        apiFetch('/history')
      ]);
      setAnalytics(await res1.json());
      setHistory(await res2.json());
    } catch (err) {
      console.error("Failed to fetch dashboard data:", err);
    }
  };

  const [userRepos, setUserRepos] = useState([]);
  const [repoLoading, setRepoLoading] = useState(false);

  const fetchUserRepos = async () => {
    setRepoLoading(true);
    try {
      const res = await apiFetch('/user-repos');
      const data = await res.json();
      setUserRepos(data.repos || []);
    } catch (err) {
      console.error('Failed to fetch repos:', err);
    } finally {
      setRepoLoading(false);
    }
  };

  const deleteRepo = async (repoName) => {
    if (!confirm(`Are you sure you want to delete ${repoName}? This cannot be undone.`)) return;
    
    try {
      const res = await apiFetch(`/delete-repo?repo_name=${encodeURIComponent(repoName)}`, { method: 'POST' });
      if (res.ok) {
        setUserRepos(prev => prev.filter(r => r !== repoName));
      }
    } catch (err) {
      alert('Failed to delete repository');
    }
  };

  useEffect(() => {
    if (view === 'dashboard') {
      fetchDashboardData();
    } else if (view === 'repos') {
      fetchUserRepos();
    }
  }, [view]);

  const getSourceInfo = (repo, filepath, dbUrl) => {
    if (dbUrl && dbUrl.startsWith('http')) {
      return {
        link: dbUrl,
        label: dbUrl.includes('github.com') ? 'GitHub' : 'External',
        icon: <ExternalLink size={14} />
      };
    }

    let cleanPath = filepath.replace(/\\/g, '/');
    if (cleanPath.startsWith(`${repo}/`)) {
      cleanPath = cleanPath.substring(repo.length + 1);
    }

    return { 
      link: '#', 
      label: 'Cloud Only',
      icon: <Terminal size={14} />
    };
  };

  const performSearch = async (searchQuery) => {
    if (!searchQuery.trim()) return;

    setLoading(true);
    setError('');
    setIsScanning(true);

    try {
      const response = await apiFetch('/search', {
        method: 'POST',
        body: JSON.stringify({ 
          query: searchQuery, 
          top_k: 4,
          ...(repoFilter ? { repo_filter: repoFilter } : {}),
          history: chatContext
        }),
      });

      if (!response.ok) {
        const errData = await response.json().catch(() => ({}));
        throw new Error(errData.detail || 'Cerebro connection failed');
      }
      const data = await response.json();
      setResults(data);
      
      setChatContext(prev => [
        ...prev, 
        { role: 'user', content: searchQuery },
        { role: 'assistant', content: data.answer }
      ]);

    } catch (err) {
      setError(err.message || 'Neural link disconnected. Check your backend server.');
    } finally {
      setLoading(false);
      setIsScanning(false);
    }
  };

  const handleSearch = async (e) => {
    e.preventDefault();
    performSearch(query);
  };

  return (
    <div className="cerebro-container">
      <div className="neural-grid"></div>

      <header className="cerebro-header">
        <div className="user-profile">
          <div className="user-avatar">
            {user.email ? user.email[0].toUpperCase() : 'U'}
          </div>
          <div className="user-info">
            <span className="user-email">{user.email}</span>
            <div style={{display: 'flex', gap: '0.5rem'}}>
              <button 
                onClick={() => setShowIngestModal(true)}
                className="ingest-nav-btn"
              >
                <GitBranch size={12} /> Connect Repo
              </button>
              <button 
                onClick={() => supabase.auth.signOut()} 
                className="signout-btn"
                title="Disconnect Neural Link"
              >
                <LogOut size={12} /> Disconnect
              </button>
            </div>
          </div>
        </div>

        <div className={`brain-icon-wrapper ${isScanning ? 'scanning' : ''}`}>
          <BrainCircuit size={48} className="brain-icon" />
          <div className="pulse-ring"></div>
        </div>
        <h1 className="cerebro-title">CEREBRO</h1>
        <p className="cerebro-subtitle">Amplifying your neural link to the codebase.</p>

        <div className="view-toggle">
          <button className={`toggle-btn ${view === 'search' ? 'active' : ''}`} onClick={() => setView('search')}>
            <Search size={16}/> Neural Search
          </button>
          <button className={`toggle-btn ${view === 'repos' ? 'active' : ''}`} onClick={() => setView('repos')}>
            <FolderDot size={16}/> Neural Vault
          </button>
          <button className={`toggle-btn ${view === 'graph' ? 'active' : ''}`} onClick={() => setView('graph')}>
            <Cpu size={16}/> Neural Map
          </button>
          <button className={`toggle-btn ${view === 'dashboard' ? 'active' : ''}`} onClick={() => setView('dashboard')}>
            <Activity size={16}/> Telemetry
          </button>
        </div>
      </header>

      <main className="cerebro-main">
        {view === 'repos' && (
          <div className="repos-view">
            <div className="view-header">
              <h2>Neural Vault</h2>
              <p>Repositories currently indexed in your neural profile.</p>
            </div>
            
            {repoLoading ? (
              <div className="loading-state">
                <Activity className="spin" /> Scanning Vault...
              </div>
            ) : (
              <div className="repo-grid">
                {userRepos.map(repo => (
                  <div key={repo} className="repo-card">
                    <div className="repo-card-header">
                      <FolderDot size={24} className="neon-icon" />
                      <button onClick={() => deleteRepo(repo)} className="delete-btn" title="Purge from memory">&times;</button>
                    </div>
                    <h3>{repo}</h3>
                    <div className="repo-meta">
                      <span className="status-badge">Indexed</span>
                    </div>
                  </div>
                ))}
                {userRepos.length === 0 && (
                  <div className="empty-state">
                    <p>No repositories found. Connect your first repo to start.</p>
                    <button onClick={() => setShowIngestModal(true)} className="ingest-submit-btn" style={{width: 'auto', padding: '0.8rem 2rem'}}>
                      <GitBranch size={16} /> Connect Nodes
                    </button>
                  </div>
                )}
              </div>
            )}
          </div>
        )}

        {view === 'graph' && (
          <NeuralMap user={user} />
        )}

        {view === 'dashboard' && (
          <div className="dashboard-view">
            {analytics ? (
              <>
                <div className="stats-grid">
                  <div className="stat-card">
                    <Search className="stat-icon" />
                    <div className="stat-value">{analytics.total_searches}</div>
                    <div className="stat-label">Total Queries</div>
                  </div>
                  <div className="stat-card">
                    <Zap className="stat-icon" />
                    <div className="stat-value">{analytics.avg_latency_ms} <span className="unit">ms</span></div>
                    <div className="stat-label">Avg Latency</div>
                  </div>
                  <div className="stat-card">
                    <ShieldCheck className="stat-icon" />
                    <div className="stat-value">{analytics.avg_confidence} <span className="unit">%</span></div>
                    <div className="stat-label">Avg Confidence</div>
                  </div>
                </div>

                <div className="history-section">
                  <h3><Clock size={18} /> Recent Neural Queries</h3>
                  <div className="history-list">
                    {history.map((item) => (
                      <div key={item.id} className="history-item">
                        <div className="history-query">{item.query}</div>
                        <div className="history-meta">
                          <span className="history-time">{new Date(item.timestamp).toLocaleTimeString()}</span>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              </>
            ) : (
              <div className="loading-state">
                <Activity className="spin" /> Loading Telemetry Data...
              </div>
            )}
          </div>
        )}

        {view === 'search' && (
          <>
            <form onSubmit={handleSearch} className="search-form">
              <div className="search-bar">
                <Search className="search-icon" size={20} />
                <input
                  type="text"
                  placeholder="Ask Cerebro anything about your codebase..."
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  className="search-input"
                />
                <button type="submit" disabled={loading} className="search-button">
                  {loading ? <Activity className="spin" size={18} /> : <ArrowRight size={18} />}
                </button>
              </div>

              <div className="filters-bar">
                <span className="filter-label">Filter Scope:</span>
                <input
                  type="text"
                  placeholder="Repository name (e.g. Cerebro)"
                  value={repoFilter}
                  onChange={(e) => setRepoFilter(e.target.value)}
                  className="repo-filter-input"
                />
              </div>
            </form>

            {error && (
              <div className="error-card">
                <p>{error}</p>
              </div>
            )}

            {results && (
              <div className="results-container">
                <div className="answer-card">
                  <div className="answer-header">
                    <Sparkles className="neon-icon" size={20} />
                    <h2>Synthesized Answer</h2>
                    <span className="confidence-badge">
                      {results.metadata?.retrievalStrategy ? `Grounded (${results.metadata.retrievalStrategy})` : 'Grounded'}
                    </span>
                  </div>
                  
                  {results.summary && (
                    <p style={{ fontSize: '0.9rem', color: '#94a3b8', fontStyle: 'italic', marginBottom: '1rem', borderBottom: '1px solid rgba(255,255,255,0.05)', paddingBottom: '0.5rem' }}>
                      <strong>Summary:</strong> {results.summary}
                    </p>
                  )}

                  <div className="answer-content">
                    {results.answer}
                  </div>

                  {results.limitations && results.limitations.length > 0 && (
                    <div style={{ marginTop: '1.25rem', borderTop: '1px solid rgba(255,255,255,0.08)', paddingTop: '0.75rem' }}>
                      <h4 style={{ fontSize: '0.8rem', color: '#f43f5e', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: '0.35rem', fontWeight: 600 }}>
                        Retrieved Context Limitations:
                      </h4>
                      <ul style={{ paddingLeft: '1.2rem', color: '#cbd5e1', fontSize: '0.82rem', listStyleType: 'square', margin: 0 }}>
                        {results.limitations.map((limit, idx) => (
                          <li key={idx} style={{ marginBottom: '0.2rem' }}>{limit}</li>
                        ))}
                      </ul>
                    </div>
                  )}

                  {results.metadata && (
                    <div style={{ display: 'flex', gap: '1rem', fontSize: '0.75rem', color: '#64748b', marginTop: '1.25rem', flexWrap: 'wrap', borderTop: '1px solid rgba(255,255,255,0.05)', paddingTop: '0.5rem' }}>
                      {results.metadata.retrievalTimeMs !== undefined && (
                        <span>🔍 Retrieval: {results.metadata.retrievalTimeMs}ms</span>
                      )}
                      {results.metadata.generationTimeMs !== undefined && (
                        <span>🧠 AI Gen: {results.metadata.generationTimeMs}ms</span>
                      )}
                      {results.metadata.totalTimeMs !== undefined && (
                        <span>⚡ Total: {results.metadata.totalTimeMs}ms</span>
                      )}
                      {results.metadata.sourcesRetrieved !== undefined && (
                        <span>📂 Sources: {results.metadata.sourcesRetrieved} (Retrieved) / {results.metadata.sourcesCited || 0} (Cited)</span>
                      )}
                    </div>
                  )}

                  {results.follow_ups && results.follow_ups.length > 0 && (
                    <div className="follow-ups-section">
                      <h4>Suggested Probes:</h4>
                      <div className="follow-up-buttons">
                        {results.follow_ups.map((q, idx) => (
                          <button key={idx} onClick={() => { setQuery(q); performSearch(q); }} className="follow-up-btn">
                            <MessageSquare size={14} /> {q}
                          </button>
                        ))}
                      </div>
                    </div>
                  )}
                </div>

                {results.sources && results.sources.length > 0 && (
                  <div className="sources-section">
                    <h3><Layers size={18} /> Supporting Code Nodes ({results.sources.length})</h3>
                    <div className="sources-grid">
                      {results.sources.map((source, idx) => {
                        const info = getSourceInfo(source.repo, source.file, source.url);
                        return (
                          <div key={idx} className="source-card">
                            <div className="source-header">
                              <span className="source-file">
                                {source.file}
                                {source.match_type && (
                                  <span style={{ fontSize: '0.72rem', padding: '0.1rem 0.4rem', borderRadius: '4px', background: 'rgba(255,255,255,0.06)', color: '#64748b', marginLeft: '0.5rem', fontWeight: 500 }}>
                                    {source.match_type} {source.retrieval_rank ? `#${source.retrieval_rank}` : ''}
                                  </span>
                                )}
                              </span>
                              {info.link !== '#' ? (
                                <a href={info.link} target="_blank" rel="noopener noreferrer" className="source-link">
                                  {info.icon} {info.label}
                                </a>
                              ) : (
                                <span className="source-link disabled">
                                  {info.icon} {info.label}
                                </span>
                              )}
                            </div>
                            <pre className="source-code">
                              <code>{source.code}</code>
                            </pre>
                          </div>
                        );
                      })}
                    </div>
                  </div>
                )}
              </div>
            )}
          </>
        )}

        {/* INGESTION MODAL */}
        {showIngestModal && (
          <div className="modal-overlay" onClick={() => setShowIngestModal(false)}>
            <div className="modal-content ingest-modal" onClick={(e) => e.stopPropagation()}>
              <div className="modal-header">
                <div style={{display: 'flex', alignItems: 'center', gap: '0.75rem'}}>
                  <GitBranch size={24} className="neon-icon" />
                  <h3>Neural Ingestion</h3>
                </div>
                <button className="close-modal" onClick={() => setShowIngestModal(false)}>&times;</button>
              </div>
              
              <p className="modal-desc">
                Connect a public GitHub repository to Cerebro. We will clone it, create semantic embeddings, and link it to your neural profile.
              </p>

              <form onSubmit={handleIngest} className="ingest-form">
                <div className="input-group">
                  <input
                    type="url"
                    placeholder="https://github.com/username/repo"
                    value={ingestUrl}
                    onChange={(e) => setIngestUrl(e.target.value)}
                    required
                    disabled={ingestStatus.loading}
                  />
                </div>
                
                <button type="submit" disabled={ingestStatus.loading} className="ingest-submit-btn">
                  {ingestStatus.loading ? (
                    <><Activity className="spin" size={18} /> Ingesting Nodes...</>
                  ) : 'Initialize Link'}
                </button>
              </form>

              {ingestStatus.loading && (
                <div className="neural-log-container">
                  <div className="terminal-header">
                    <div className="dot red"></div>
                    <div className="dot yellow"></div>
                    <div className="dot green"></div>
                    <span className="terminal-title">Neural Status Terminal</span>
                  </div>
                  <div className="terminal-body">
                    {ingestStatus.logs.map((log, i) => (
                      <div key={i} className="terminal-line animate-slide-in">
                        <span className="terminal-prompt">&gt;</span> {log}
                      </div>
                    ))}
                    <div className="terminal-line pulse-line">
                      <span className="terminal-prompt">&gt;</span> _
                    </div>
                  </div>
                </div>
              )}

              {ingestStatus.error && <div className="modal-message error">{ingestStatus.error}</div>}
              {ingestStatus.success && <div className="modal-message success">{ingestStatus.success}</div>}
            </div>
          </div>
        )}
      </main>
    </div>
  );
}
