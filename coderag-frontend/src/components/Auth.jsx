import React, { useState } from 'react';
import { supabase } from '../supabaseClient';
import { BrainCircuit, Mail, Lock, UserPlus, LogIn, GitBranch } from 'lucide-react';

import './Auth.css';

export default function Auth({ onClose, dialogTitleId }) {
  const [loading, setLoading]   = useState(false);
  const [email, setEmail]       = useState('');
  const [password, setPassword] = useState('');
  const [isSignUp, setIsSignUp] = useState(false);
  const [message, setMessage]   = useState(null);   // { type: 'success'|'error', text: string }

  const errorId = 'auth-error-msg';

  const handleAuth = async (e) => {
    e.preventDefault();
    setLoading(true);
    setMessage(null);
    try {
      if (isSignUp) {
        const { error } = await supabase.auth.signUp({ email, password });
        if (error) throw error;
        setMessage({ type: 'success', text: 'Check your email for a confirmation link.' });
      } else {
        const { error } = await supabase.auth.signInWithPassword({ email, password });
        if (error) throw error;
        // Session will be picked up by onAuthStateChange → App will re-render
      }
    } catch (err) {
      // Only expose the message Supabase gives (user-safe, not credentials)
      setMessage({ type: 'error', text: err.message || 'Authentication failed. Please try again.' });
    } finally {
      setLoading(false);
    }
  };

  const handleGitHubLogin = async () => {
    try {
      const { error } = await supabase.auth.signInWithOAuth({ provider: 'github' });
      if (error) throw error;
    } catch (err) {
      setMessage({ type: 'error', text: err.message || 'GitHub sign-in failed. Please try again.' });
    }
  };

  return (
    <div className="auth-card">
      {/* Header */}
      <div className="auth-header">
        <BrainCircuit size={36} className="auth-brain-icon" aria-hidden="true" />
        <h2 id={dialogTitleId} className="auth-title">
          {isSignUp ? 'Create your account' : 'Sign in to Cerebro'}
        </h2>
        <p className="auth-subtitle">
          {isSignUp
            ? 'Start indexing and querying your codebase.'
            : 'Access your indexed repositories and code intelligence.'}
        </p>
      </div>

      {/* GitHub OAuth — primary */}
      <button
        type="button"
        className="auth-github-btn"
        onClick={handleGitHubLogin}
        disabled={loading}
      >
        <GitBranch size={18} aria-hidden="true" />

        Continue with GitHub
      </button>

      <div className="auth-divider" role="separator" aria-label="or sign in with email">
        <span>or</span>
      </div>

      {/* Email / password form */}
      <form onSubmit={handleAuth} className="auth-form" noValidate>
        <div className="auth-field">
          <label htmlFor="auth-email" className="auth-label">
            <Mail size={14} aria-hidden="true" />
            Email address
          </label>
          <input
            id="auth-email"
            type="email"
            className="auth-input"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            autoComplete={isSignUp ? 'email' : 'username'}
            required
            disabled={loading}
            aria-describedby={message?.type === 'error' ? errorId : undefined}
          />
        </div>

        <div className="auth-field">
          <label htmlFor="auth-password" className="auth-label">
            <Lock size={14} aria-hidden="true" />
            Password
          </label>
          <input
            id="auth-password"
            type="password"
            className="auth-input"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete={isSignUp ? 'new-password' : 'current-password'}
            required
            disabled={loading}
            minLength={isSignUp ? 6 : undefined}
          />
        </div>

        <button
          type="submit"
          className="auth-submit-btn"
          disabled={loading || !email || !password}
        >
          {loading
            ? 'Processing…'
            : isSignUp
              ? <><UserPlus size={16} aria-hidden="true" /> Create account</>
              : <><LogIn size={16} aria-hidden="true" /> Sign in</>
          }
        </button>
      </form>

      {/* Message */}
      {message && (
        <div
          id={errorId}
          className={`auth-message auth-message--${message.type}`}
          role={message.type === 'error' ? 'alert' : 'status'}
          aria-live={message.type === 'error' ? 'assertive' : 'polite'}
        >
          {message.text}
        </div>
      )}

      {/* Toggle */}
      <div className="auth-toggle">
        <button
          type="button"
          className="auth-toggle-btn"
          onClick={() => { setIsSignUp(!isSignUp); setMessage(null); }}
        >
          {isSignUp
            ? 'Already have an account? Sign in'
            : "Don't have an account? Create one"}
        </button>
      </div>
    </div>
  );
}
