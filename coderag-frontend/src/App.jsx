import React, { useState, useEffect } from 'react';
import { supabase, isSupabaseConfigured } from './supabaseClient';
import Cerebro from './components/CodeRAG';
import LandingPage from './components/LandingPage';
import './index.css';

export default function App() {
  const [session, setSession] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!isSupabaseConfigured) {
      setLoading(false);
      return;
    }

    supabase.auth.getSession().then(({ data: { session } }) => {
      setSession(session);
      setLoading(false);
    }).catch(() => {
      setLoading(false);
    });

    const { data: { subscription } } = supabase.auth.onAuthStateChange((_event, session) => {
      setSession(session);
    });

    return () => subscription.unsubscribe();
  }, []);

  /* Missing configuration */
  if (!isSupabaseConfigured) {
    return (
      <div className="config-error-screen" role="main">
        <div className="config-error-card" role="alert">
          <h1>
            <span aria-hidden="true">⚠</span>
            {' '}Configuration Required
          </h1>
          <p>
            Cerebro requires Supabase environment variables. Add them to your{' '}
            <code>.env</code> file or deployment settings to continue.
          </p>
          <div className="config-code-block" aria-label="Required environment variables">
            <div>VITE_SUPABASE_URL=https://your-project.supabase.co</div>
            <div>VITE_SUPABASE_ANON_KEY=your-anon-key</div>
          </div>
          <p>
            Set these variables and restart the development server, or configure them in
            your deployment dashboard.
          </p>
        </div>
      </div>
    );
  }

  /* Session loading */
  if (loading) {
    return (
      <div className="loading-screen" role="status" aria-live="polite" aria-label="Loading Cerebro">
        <div className="loading-spinner" aria-hidden="true" />
        <span className="loading-text">Loading Cerebro…</span>
      </div>
    );
  }

  return (
    <div className="app-container">
      {session
        ? <Cerebro user={session.user} />
        : <LandingPage />
      }
    </div>
  );
}
