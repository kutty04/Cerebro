import React, { useState } from 'react';
import { Terminal, Brain, Share2, Shield, Zap, ChevronRight, GitBranch } from 'lucide-react';
import Auth from './Auth';

export default function LandingPage() {
  const [showAuth, setShowAuth] = useState(false);

  const features = [
    {
      icon: <Brain size={24} />,
      name: "Neural Code Mapping",
      desc: "Visualize your entire codebase as an interactive, semantic graph. Understand relationships instantly."
    },
    {
      icon: <Terminal size={24} />,
      name: "Conversational RAG",
      desc: "Chat with your code. Ask questions, find bugs, and explain logic using advanced Retrieval-Augmented Generation."
    },
    {
      icon: <Zap size={24} />,
      name: "Serverless Speed",
      desc: "Near-instant ingestion and search powered by Groq Llama 3 and high-speed vector embeddings."
    }
  ];

  return (
    <div className="landing-page">
      <div className="hero-section">
        <div className="hero-badge">CEREBRO V2.0 IS LIVE</div>
        <h1 className="hero-title">The Neural Operating System for your Codebase</h1>
        <p className="hero-subtitle">
          Connect your GitHub repositories to a high-speed neural link. Index, visualize, and chat with your code in a fully secure, serverless environment.
        </p>
        
        <button 
          className="ingest-submit-btn" 
          onClick={() => setShowAuth(true)}
          style={{ padding: '1rem 2.5rem', fontSize: '1.1rem', gap: '1rem' }}
        >
          <GitBranch size={20} /> Initiate Neural Link <ChevronRight size={20} />
        </button>
      </div>

      <div className="features-grid">
        {features.map((f, i) => (
          <div className="feature-card" key={i}>
            <div className="feature-icon">{f.icon}</div>
            <h3 className="feature-name">{f.name}</h3>
            <p className="feature-desc">{f.desc}</p>
          </div>
        ))}
      </div>

      <div className="landing-footer" style={{ marginTop: '8rem', textAlign: 'center', color: 'var(--text-muted)', fontSize: '0.9rem' }}>
        <div style={{ display: 'flex', gap: '2rem', justifyContent: 'center', marginBottom: '2rem' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}><Shield size={16} /> Secure by Design</div>
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}><Zap size={16} /> Serverless Infrastructure</div>
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}><Share2 size={16} /> Multi-user Ready</div>
        </div>
        <p>&copy; 2024 Cerebro AI Neural Systems. All rights reserved.</p>
      </div>

      {showAuth && (
        <div className="auth-overlay" onClick={() => setShowAuth(false)}>
          <div onClick={e => e.stopPropagation()} style={{ width: '100%', maxWidth: '450px' }}>
            <Auth />
          </div>
        </div>
      )}
    </div>
  );
}
