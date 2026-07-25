import React, { useState } from 'react';
import { Search, Loader, AlertCircle, Copy, ExternalLink } from 'lucide-react';
import './CodeRAG.css';

const CodeRAG = () => {
  const [query, setQuery] = useState('');
  const [loading, setLoading] = useState(false);
  const [response, setResponse] = useState(null);
  const [error, setError] = useState(null);
  const [topK, setTopK] = useState(5);
  const [copiedSnippet, setCopiedSnippet] = useState(null);

  const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000';

  const handleSearch = async (e) => {
    e.preventDefault();
    if (!query.trim()) return;

    setLoading(true);
    setError(null);
    setResponse(null);

    try {
      const res = await fetch(`${API_URL}/search`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query, top_k: topK }),
      });

      if (!res.ok) {
        throw new Error(`API error: ${res.statusText}`);
      }

      const data = await res.json();
      setResponse(data);
    } catch (err) {
      setError(err.message || 'Failed to search. Check if backend is running.');
      console.error('Search error:', err);
    } finally {
      setLoading(false);
    }
  };

  const copyToClipboard = (text, snippetId) => {
    navigator.clipboard.writeText(text);
    setCopiedSnippet(snippetId);
    setTimeout(() => setCopiedSnippet(null), 2000);
  };

  const getLanguageColor = (language) => {
    const colors = {
      python: '#3776ab',
      javascript: '#f7df1e',
      typescript: '#3178c6',
      dart: '#0175c2',
      java: '#007396',
      cpp: '#00599c',
      go: '#00add8',
      rust: '#ce422b',
      sql: '#336791',
      html: '#e34c26',
      css: '#563d7c',
      markdown: '#083fa1',
    };
    return colors[language] || '#666';
  };

  return (
    <div className="coderag-container">
      {/* Header */}
      <header className="coderag-header">
        <div className="header-content">
          <h1 className="logo">
            <span className="logo-icon">{'</>'}</span>
            CodeRAG
          </h1>
          <p className="tagline">Semantic search across your codebase</p>
        </div>
      </header>

      {/* Main Content */}
      <main className="coderag-main">
        {/* Search Section */}
        <div className="search-section">
          <form onSubmit={handleSearch} className="search-form">
            <div className="search-input-wrapper">
              <Search size={20} className="search-icon" />
              <input
                type="text"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Ask anything about your code... e.g., 'How do I handle GPS in Flutter?'"
                className="search-input"
                disabled={loading}
              />
            </div>

            <div className="search-controls">
              <select
                value={topK}
                onChange={(e) => setTopK(parseInt(e.target.value))}
                className="topk-select"
                disabled={loading}
              >
                <option value={3}>3 results</option>
                <option value={5}>5 results</option>
                <option value={10}>10 results</option>
              </select>

              <button type="submit" className="search-button" disabled={loading}>
                {loading ? (
                  <>
                    <Loader size={18} className="spinner" />
                    Searching...
                  </>
                ) : (
                  <>
                    <Search size={18} />
                    Search
                  </>
                )}
              </button>
            </div>
          </form>

          {/* Info Box */}
          <div className="info-box">
            <p>💡 Tip: Be specific. "GPS location handling" gets better results than "GPS"</p>
          </div>
        </div>

        {/* Results Section */}
        {error && (
          <div className="error-box">
            <AlertCircle size={20} />
            <p>{error}</p>
          </div>
        )}

        {response && (
          <div className="results-section">
            {/* AI Answer */}
            <div className="ai-response">
              <h2>💡 Answer</h2>
              <p className="answer-text">{response.answer}</p>
            </div>

            {/* Source Snippets */}
            <div className="sources-section">
              <h2>📚 Source Code ({response.sources.length})</h2>

              {response.sources.length === 0 ? (
                <p className="no-sources">No matching code snippets found.</p>
              ) : (
                <div className="snippets-list">
                  {response.sources.map((source, idx) => (
                    <div key={idx} className="snippet-card">
                      {/* Snippet Header */}
                      <div className="snippet-header">
                        <div className="snippet-meta">
                          <span
                            className="language-badge"
                            style={{
                              backgroundColor: getLanguageColor(source.language),
                            }}
                          >
                            {source.language.toUpperCase()}
                          </span>
                          <span className="file-path">{source.file}</span>
                          <span className="repo-name">📦 {source.repo}</span>
                        </div>

                        <div className="snippet-actions">
                          <button
                            className="action-button"
                            onClick={() => copyToClipboard(source.code, idx)}
                            title="Copy code"
                          >
                            {copiedSnippet === idx ? '✓' : <Copy size={16} />}
                          </button>

                          {source.url && (
                            <a
                              href={source.url}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="action-button"
                              title="Open file"
                            >
                              <ExternalLink size={16} />
                            </a>
                          )}
                        </div>
                      </div>

                      {/* Snippet Code */}
                      <pre className="code-block">
                        <code>{source.code}</code>
                      </pre>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        )}

        {/* Empty State */}
        {!response && !error && !loading && (
          <div className="empty-state">
            <div className="empty-icon">{'</>'}</div>
            <h2>Start searching your codebase</h2>
            <p>Ask questions about your code and get instant answers with relevant snippets</p>

            <div className="example-queries">
              <p className="example-label">Try asking:</p>
              <div className="example-buttons">
                <button onClick={() => setQuery("How do I handle errors?")} className="example-btn">
                  How do I handle errors?
                </button>
                <button onClick={() => setQuery("Show me database queries")} className="example-btn">
                  Show me database queries
                </button>
                <button onClick={() => setQuery("Authentication logic")} className="example-btn">
                  Authentication logic
                </button>
              </div>
            </div>
          </div>
        )}
      </main>

      {/* Footer */}
      <footer className="coderag-footer">
        <p>CodeRAG — Powered by Vector Search + LLMs</p>
      </footer>
    </div>
  );
};

export default CodeRAG;
