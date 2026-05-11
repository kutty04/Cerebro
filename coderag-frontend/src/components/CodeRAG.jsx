import React, { useState, useEffect } from 'react';
import { Search, BrainCircuit, Terminal, Cpu, Zap, FolderDot, Copy, Check, ExternalLink, Activity, GitBranch } from 'lucide-react';
import Auth from './Auth';
import NeuralMap from './NeuralMap';
import './CodeRAG.css';

import ReactMarkdown from 'react-markdown';
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter';
import { atomDark } from 'react-syntax-highlighter/dist/esm/styles/prism';

const CodeBlockWithCopy = ({ match, children, props }) => {
  const [copied, setCopied] = useState(false);

  const handleCopy = () => {
    navigator.clipboard.writeText(String(children).replace(/\n$/, ''));
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div className="code-block-wrapper">
      <div className="code-block-header">
        <span className="code-language">{match[1]}</span>
        <button onClick={handleCopy} className="copy-button" aria-label="Copy code">
          {copied ? <Check size={16} /> : <Copy size={16} />}
        </button>
      </div>
      <SyntaxHighlighter
        style={atomDark}
        language={match[1]}
        PreTag="div"
        customStyle={{ margin: 0, borderTopLeftRadius: 0, borderTopRightRadius: 0 }}
        {...props}
      >
        {String(children).replace(/\n$/, '')}
      </SyntaxHighlighter>
    </div>
  );
};

import { supabase } from '../supabaseClient';

export default function Cerebro({ user }) {
  const [query, setQuery] = useState('');
  const [repoFilter, setRepoFilter] = useState('');
  const [results, setResults] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  
  const [view, setView] = useState('search');
  const [analytics, setAnalytics] = useState(null);
  const [history, setHistory] = useState([]);
  const [isScanning, setIsScanning] = useState(false);
  const [chatContext, setChatContext] = useState([]);
  const [showIngestModal, setShowIngestModal] = useState(false);
  const [ingestUrl, setIngestUrl] = useState('');
  const [ingestStatus, setIngestStatus] = useState({ loading: false, error: '', success: '' });

  const apiUrl = import.meta.env.VITE_API_URL || 'http://localhost:8000';

  const handleIngest = async (e) => {
    e.preventDefault();
    if (!ingestUrl.trim()) return;

    setIngestStatus({ loading: true, error: '', success: '' });
    try {
      const apiUrl = import.meta.env.VITE_API_URL || 'http://localhost:8000';
      const response = await fetch(`${apiUrl}/ingest`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ 
          repo_url: ingestUrl, 
          user_id: user.id 
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
    } catch (err) {
      setIngestStatus({ loading: false, error: err.message, success: '' });
    }
  };

  const fetchDashboardData = async () => {
    try {
      const apiUrl = import.meta.env.VITE_API_URL || 'http://localhost:8000';
      const [res1, res2] = await Promise.all([
        fetch(`${apiUrl}/analytics`),
        fetch(`${apiUrl}/history`)
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
      const apiUrl = import.meta.env.VITE_API_URL || 'http://localhost:8000';
      const res = await fetch(`${apiUrl}/user-repos?user_id=${user.id}`);
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
      const apiUrl = import.meta.env.VITE_API_URL || 'http://localhost:8000';
      const res = await fetch(`${apiUrl}/delete-repo?repo_name=${repoName}&user_id=${user.id}`, { method: 'POST' });
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

  const getSourceInfo = (repo, filepath) => {
    let cleanPath = filepath.replace(/\\/g, '/');
    if (cleanPath.startsWith(`${repo}/`)) {
      cleanPath = cleanPath.substring(repo.length + 1);
    }

    // CONFIG: Map your repositories here (either local paths or github urls)
    const repoMap = {
      'F1-intelligence': { type: 'local', path: 'C:/Users/R.Murugesan/OneDrive/Desktop/coderag-data/F1-intelligence' },
      'focussense-ai': { type: 'github', url: 'https://github.com/rmurugesan/focussense-ai' },
      'ipl': { type: 'github', url: 'https://github.com/rmurugesan/ipl' }
    };

    const config = repoMap[repo];
    
    if (config && config.type === 'github') {
      return { 
        link: `${config.url}/blob/main/${cleanPath}`, 
        label: 'GitHub',
        icon: <ExternalLink size={14} />
      };
    }
    
    // Default to local VS Code Link
    const localBasePath = config?.path || `C:/Users/R.Murugesan/OneDrive/Desktop/coderag-data/${repo}`;
    return { 
      link: `vscode://file/${localBasePath}/${cleanPath}`, 
      label: 'VS Code',
      icon: <Terminal size={14} />
    };
  };

  // Simulating neural connection effect
  useEffect(() => {
    const scanInterval = setInterval(() => {
      setIsScanning(prev => !prev);
    }, 3000);
    return () => clearInterval(scanInterval);
  }, []);

  const performSearch = async (searchQuery) => {
    if (!searchQuery.trim()) return;

    setLoading(true);
    setError('');
    setResults(null);

    try {
      const apiUrl = import.meta.env.VITE_API_URL || 'http://localhost:8000';
      const response = await fetch(`${apiUrl}/search`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ 
          query: searchQuery, 
          top_k: 4,
          user_id: user.id,
          ...(repoFilter ? { repo_filter: repoFilter } : {}),
          history: chatContext
        }),
      });

      if (!response.ok) throw new Error('Cerebro connection failed');
      const data = await response.json();
      setResults(data);
      
      // Update local context for next turn
      setChatContext(prev => [
        ...prev, 
        { role: 'user', content: searchQuery },
        { role: 'assistant', content: data.answer }
      ]);

    } catch (err) {
      setError(err.message || 'Neural link disconnected. Check your backend server.');
    } finally {
      setLoading(false);
    }
  };

  const handleSearch = async (e) => {
    e.preventDefault();
    performSearch(query);
  };

  return (
    <div className="cerebro-container">
      {/* Background Neural Grid */}
      <div className="neural-grid"></div>

      <header className="cerebro-header">
        <div className="user-profile">
          <div className="user-avatar">
            {user.email[0].toUpperCase()}
          </div>
          <div className="user-info">
            <span className="user-email">{user.email}</span>
            <div style={{display: 'flex', gap: '0.5rem'}}>
              <button onClick={() => setShowIngestModal(true)} className="ingest-nav-btn">
                <GitBranch size={12} /> Import Repo
              </button>
              <button onClick={() => supabase.auth.signOut()} className="logout-btn">
                Disconnect
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

        {view === 'search' && (
          <>
            <form onSubmit={handleSearch} className="search-box">
              <div className="input-wrapper">
                <Search className="search-icon" size={20} />
                <input
                  type="text"
                  placeholder="Read the mind of your past projects... (e.g., 'How do I handle GPS in the bus app?')"
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  className="search-input"
                  autoFocus
                />
                {loading && <div className="scanning-beam"></div>}
              </div>
              <select 
                value={repoFilter} 
                onChange={(e) => setRepoFilter(e.target.value)}
                className="repo-select"
              >
                <option value="">All Projects</option>
                <option value="F1-intelligence">F1 Intelligence</option>
                <option value="focussense-ai">FocusSense AI</option>
                <option value="ipl">IPL Predictor</option>
              </select>
              <button type="submit" disabled={loading} className="search-button">
                {loading ? <Zap className="spin" /> : 'Connect'}
              </button>
            </form>

            {error && (
              <div className="error-message">
                <Terminal size={18} />
                <span>{error}</span>
              </div>
            )}

            {results && (
              <div className="results-container">
                <div className="ai-insight-card">
                  <div className="card-header ai-header" style={{display: 'flex', justifyContent: 'space-between', width: '100%'}}>
                    <div style={{display: 'flex', alignItems: 'center', gap: '0.5rem'}}>
                      <Cpu size={20} className="neon-icon" />
                      <h3>Cerebro Insight</h3>
                    </div>
                    {results.confidence > 0 && (
                      <div className={`confidence-badge ${results.confidence > 80 ? 'high' : results.confidence > 60 ? 'medium' : 'low'}`} title="AI Confidence Score">
                        <Activity size={14} />
                        <span>{results.confidence}% Match</span>
                      </div>
                    )}
                  </div>
                  <div className="ai-answer-wrapper">
                    <ReactMarkdown
                      components={{
                        code({ node, inline, className, children, ...props }) {
                          const match = /language-(\w+)/.exec(className || '');
                          return !inline && match ? (
                            <CodeBlockWithCopy match={match} props={props}>
                              {children}
                            </CodeBlockWithCopy>
                          ) : (
                            <code className={className} {...props}>
                              {children}
                            </code>
                          );
                        }
                      }}
                    >
                      {results.answer}
                    </ReactMarkdown>
                  </div>

                  {results.follow_ups && results.follow_ups.length > 0 && (
                    <div className="follow-ups-container">
                      <p className="follow-ups-title">💡 Suggested Follow-ups:</p>
                      <div className="follow-ups-list">
                        {results.follow_ups.map((question, idx) => (
                          <button 
                            key={idx} 
                            className="follow-up-chip"
                            onClick={() => {
                              setQuery(question);
                              performSearch(question);
                            }}
                          >
                            {question}
                          </button>
                        ))}
                      </div>
                    </div>
                  )}
                </div>

                <div className="snippets-grid">
                  {results.sources.map((source, idx) => (
                    <div key={idx} className="snippet-card">
                      <div className="card-header" style={{display: 'flex', justifyContent: 'space-between', alignItems: 'center'}}>
                        <div style={{display: 'flex', alignItems: 'center', gap: '0.5rem'}}>
                          <FolderDot size={16} className="file-icon" />
                          <span className="repo-name">{source.repo}</span>
                          <span className="file-path">/{source.file}</span>
                        </div>
                        {(() => {
                          const sourceInfo = getSourceInfo(source.repo, source.file);
                          return (
                            <a 
                              href={sourceInfo.link} 
                              target={sourceInfo.label === 'GitHub' ? "_blank" : "_self"} 
                              rel={sourceInfo.label === 'GitHub' ? "noopener noreferrer" : ""}
                              className="github-link"
                              title={`Open in ${sourceInfo.label}`}
                            >
                              {sourceInfo.icon} {sourceInfo.label}
                            </a>
                          );
                        })()}
                      </div>
                      <div className="code-container">
                        <pre>
                          <code>{source.code}</code>
                        </pre>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {!results && !loading && !error && (
              <div className="empty-state">
                <p>"Just as Cerebro finds any mutant on Earth, we will find the exact logic you need."</p>
              </div>
            )}
          </>
        )}

        {view === 'dashboard' && (
          <div className="dashboard-container">
            {analytics && (
              <div className="stats-grid">
                <div className="stat-card">
                  <h4>Total Queries</h4>
                  <p className="stat-value">{analytics.total_searches}</p>
                </div>
                <div className="stat-card">
                  <h4>Avg Latency</h4>
                  <p className="stat-value">{analytics.avg_latency_ms} ms</p>
                </div>
                <div className="stat-card">
                  <h4>Avg Confidence</h4>
                  <p className="stat-value">{analytics.avg_confidence}%</p>
                </div>
              </div>
            )}
            
            <div className="history-section">
              <h3>Recent Neural Syncs (Chat History)</h3>
              <div className="history-list">
                {history.map((item, idx) => (
                  <div key={idx} className="history-item">
                    <div className="history-q">
                      <Terminal size={14}/> {item.query}
                    </div>
                    <div className="history-a">
                      {item.answer.substring(0, 200)}...
                    </div>
            <div className="history-meta">{item.timestamp}</div>
                  </div>
                ))}
                {history.length === 0 && <p className="text-muted">No history found. Run a query first!</p>}
              </div>
            </div>
          </div>
        )}

        {showIngestModal && (
          <div className="modal-overlay">
            <div className="modal-content ingest-modal">
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
                <div className="ingest-progress">
                  <div className="progress-bar-container">
                    <div className="progress-bar-fill"></div>
                  </div>
                  <span>Cloning and Vectorizing... This may take a moment.</span>
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
