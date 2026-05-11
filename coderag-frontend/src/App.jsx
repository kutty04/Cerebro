import React, { useState, useEffect } from 'react';
import { supabase } from './supabaseClient';
import Cerebro from './components/CodeRAG';
import LandingPage from './components/LandingPage';
import './components/CodeRAG.css'; // Ensure button styles are available
import './index.css';

export default function App() {
  const [session, setSession] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    // Check current session
    supabase.auth.getSession().then(({ data: { session } }) => {
      setSession(session);
      setLoading(false);
    });

    // Listen for auth changes
    const { data: { subscription } } = supabase.auth.onAuthStateChange((_event, session) => {
      setSession(session);
    });

    return () => subscription.unsubscribe();
  }, []);

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
