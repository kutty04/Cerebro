import React, { useState, useEffect } from 'react';
import { supabase, isSupabaseConfigured } from './supabaseClient';
import Cerebro from './components/CodeRAG';
import LandingPage from './components/LandingPage';
import './components/CodeRAG.css';
import './index.css';

export default function App() {
  const [session, setSession] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!isSupabaseConfigured) {
      setLoading(false);
      return;
    }

    // Check current session
    supabase.auth.getSession().then(({ data: { session } }) => {
      setSession(session);
      setLoading(false);
    }).catch(err => {
      console.error('Supabase auth session error:', err);
      setLoading(false);
    });

    // Listen for auth changes
    const { data: { subscription } } = supabase.auth.onAuthStateChange((_event, session) => {
      setSession(session);
    });

    return () => subscription.unsubscribe();
  }, []);

  if (!isSupabaseConfigured) {
    return (
      <div className="loading-screen" style={{ flexDirection: 'column', padding: '2rem', textAlign: 'center' }}>
        <div className="neural-grid"></div>
        <div className="config-error-card" style={{
          background: 'rgba(15, 23, 42, 0.9)',
          border: '1px solid rgba(239, 68, 68, 0.4)',
          borderRadius: '16px',
          padding: '2.5rem',
          maxWidth: '550px',
          color: '#f8fafc',
          boxShadow: '0 20px 25px -5px rgba(0, 0, 0, 0.5)'
        }}>
          <h2 style={{ color: '#ef4444', marginBottom: '1rem', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '0.5rem' }}>
            ⚠️ Configuration Required
          </h2>
          <p style={{ color: '#94a3b8', fontSize: '0.95rem', lineHeight: '1.6', marginBottom: '1.5rem' }}>
            Cerebro AI requires Supabase environment variables to connect to your neural profile.
          </p>
          <div style={{
            background: 'rgba(0, 0, 0, 0.5)',
            padding: '1rem',
            borderRadius: '8px',
            textAlign: 'left',
            fontFamily: 'monospace',
            fontSize: '0.85rem',
            color: '#38bdf8',
            marginBottom: '1.5rem'
          }}>
            <div>VITE_SUPABASE_URL=https://your-project.supabase.co</div>
            <div>VITE_SUPABASE_ANON_KEY=your-anon-key</div>
          </div>
          <p style={{ color: '#64748b', fontSize: '0.85rem' }}>
            Configure these variables in your deployment settings or local <code style={{ color: '#cbd5e1' }}>.env</code> file to activate the link.
          </p>
        </div>
      </div>
    );
  }

  if (loading) {
    return (
      <div className="loading-screen">
        <div className="neural-grid"></div>
        <div className="pulse-loader">Initializing Cerebro Neural Link...</div>
      </div>
    );
  }

  return (
    <div className="app-container">
      {!session ? <LandingPage /> : <Cerebro user={session.user} />}
    </div>
  );
}
