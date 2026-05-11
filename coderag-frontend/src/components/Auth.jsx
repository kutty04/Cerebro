import React, { useState } from 'react';
import { supabase } from '../supabaseClient';
import { BrainCircuit, Mail, Lock, UserPlus, LogIn, GitBranch } from 'lucide-react';
import './Auth.css';

export default function Auth() {
  const [loading, setLoading] = useState(false);
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [isSignUp, setIsSignUp] = useState(false);
  const [message, setMessage] = useState(null);

  const handleAuth = async (e) => {
    e.preventDefault();
    setLoading(true);
    setMessage(null);
    
    try {
      if (isSignUp) {
        const { error } = await supabase.auth.signUp({ email, password });
        if (error) throw error;
        setMessage({ type: 'success', text: 'Check your email for the confirmation link!' });
      } else {
        const { error } = await supabase.auth.signInWithPassword({ email, password });
        if (error) throw error;
      }
    } catch (error) {
      setMessage({ type: 'error', text: error.message });
    } finally {
      setLoading(false);
    }
  };

  const handleGitHubLogin = async () => {
    try {
      const { error } = await supabase.auth.signInWithOAuth({ provider: 'github' });
      if (error) throw error;
    } catch (error) {
      setMessage({ type: 'error', text: error.message });
    }
  };

  return (
    <div className="auth-container">
      <div className="neural-grid"></div>
      <div className="auth-card">
        <div className="auth-header">
          <div className="brain-icon-wrapper scanning">
            <BrainCircuit size={48} className="brain-icon" />
          </div>
          <h1>CEREBRO</h1>
          <p>{isSignUp ? 'Create your neural profile' : 'Reconnect to the neural link'}</p>
        </div>

        <form onSubmit={handleAuth} className="auth-form">
          <div className="input-group">
            <Mail className="input-icon" size={18} />
            <input
              type="email"
              placeholder="Email Address"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
            />
          </div>
          <div className="input-group">
            <Lock className="input-icon" size={18} />
            <input
              type="password"
              placeholder="Password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
            />
          </div>

          <button type="submit" disabled={loading} className="auth-button">
            {loading ? 'Processing...' : isSignUp ? (
              <><UserPlus size={18} /> Initialize Account</>
            ) : (
              <><LogIn size={18} /> Connect Link</>
            )}
          </button>
        </form>

        <div className="auth-divider">
          <span>OR</span>
        </div>

        <button onClick={handleGitHubLogin} className="github-button">
          <GitBranch size={18} /> Continue with GitHub
        </button>

        {message && (
          <div className={`auth-message ${message.type}`}>
            {message.text}
          </div>
        )}

        <div className="auth-footer">
          <button onClick={() => setIsSignUp(!isSignUp)}>
            {isSignUp ? 'Already have a profile? Sign In' : 'New mutant? Create a profile'}
          </button>
        </div>
      </div>
    </div>
  );
}
