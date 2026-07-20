import React, { useState, useEffect, useRef } from 'react';
import {
  Shield, Zap, GitBranch, Database, Search, Clock,
  Lock, LogIn, UserPlus, Mail, BrainCircuit
} from 'lucide-react';
import Auth from './Auth';

/* ── Focus trap utility ─────────────────────────────────── */
function useFocusTrap(ref, active) {
  useEffect(() => {
    if (!active || !ref.current) return;
    const el = ref.current;
    const focusable = el.querySelectorAll(
      'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'
    );
    const first = focusable[0];
    const last  = focusable[focusable.length - 1];

    function handleKeyDown(e) {
      if (e.key !== 'Tab') return;
      if (e.shiftKey) {
        if (document.activeElement === first) {
          e.preventDefault();
          last.focus();
        }
      } else {
        if (document.activeElement === last) {
          e.preventDefault();
          first.focus();
        }
      }
    }
    el.addEventListener('keydown', handleKeyDown);
    first?.focus();
    return () => el.removeEventListener('keydown', handleKeyDown);
  }, [active, ref]);
}

/* ── Landing page features ─────────────────────────────── */
const FEATURES = [
  {
    icon: <Search size={20} aria-hidden="true" />,
    name: 'Grounded Code Search',
    desc: 'Ask questions about your codebase and receive answers verified against real indexed snippets — no hallucinated file paths or symbols.',
  },
  {
    icon: <Shield size={20} aria-hidden="true" />,
    name: 'Strict User Isolation',
    desc: 'Every repository, index, and answer is scoped to your authenticated identity. No cross-user data access.',
  },
  {
    icon: <GitBranch size={20} aria-hidden="true" />,
    name: 'Secure Repository Indexing',
    desc: 'Connect public GitHub repositories. Cerebro clones, chunks, and embeds your code with idempotent versioned indexes.',
  },
  {
    icon: <Database size={20} aria-hidden="true" />,
    name: 'Verified Source Citations',
    desc: 'Every answer includes the exact file, line range, and retrieval rank of the source snippets used.',
  },
  {
    icon: <Clock size={20} aria-hidden="true" />,
    name: 'Retrieval Timing',
    desc: 'See retrieval, generation, and total response times. Understand exactly how your answer was produced.',
  },
  {
    icon: <BrainCircuit size={20} aria-hidden="true" />,
    name: 'Repository Knowledge Map',
    desc: 'Visualise indexed file-to-repository relationships as an interactive graph, with an accessible table fallback.',
  },
];

export default function LandingPage() {
  const [showAuth, setShowAuth] = useState(false);
  const authRef   = useRef(null);
  const triggerRef = useRef(null);

  useFocusTrap(authRef, showAuth);

  function openAuth() { setShowAuth(true); }
  function closeAuth() {
    setShowAuth(false);
    triggerRef.current?.focus();
  }

  /* Escape key */
  useEffect(() => {
    if (!showAuth) return;
    function onKey(e) { if (e.key === 'Escape') closeAuth(); }
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [showAuth]);

  return (
    <>
      <div className="landing-page">
        {/* ── Nav ─────────────────────────────────────── */}
        <nav className="landing-nav" aria-label="Cerebro navigation">
          <a href="/" className="landing-logo" aria-label="Cerebro home">
            <div className="landing-logo-mark" aria-hidden="true">
              <BrainCircuit size={20} />
            </div>
            <span className="landing-logo-text">CEREBRO</span>
          </a>
          <button
            ref={triggerRef}
            id="nav-signin-btn"
            className="landing-nav-cta"
            onClick={openAuth}
          >
            <LogIn size={16} aria-hidden="true" />
            <span>Sign in</span>
          </button>
        </nav>

        {/* ── Hero ────────────────────────────────────── */}
        <section className="hero-section" aria-labelledby="hero-heading">
          <div className="hero-eyebrow" aria-label="Product status">
            <Lock size={12} aria-hidden="true" />
            Authenticated Code Intelligence
          </div>
          <h1 id="hero-heading" className="hero-title">
            Ask your codebase.<br />
            Get <em>grounded</em> answers.
          </h1>
          <p className="hero-subtitle">
            Cerebro indexes your GitHub repositories with secure, user-isolated
            embeddings and returns answers cited to real source snippets — not guesses.
          </p>
          <div className="hero-actions">
            <button
              className="btn-primary"
              onClick={openAuth}
              aria-label="Sign in to Cerebro"
            >
              <GitBranch size={18} aria-hidden="true" />
              Get started
            </button>
            <a
              href="https://github.com"
              className="btn-secondary"
              target="_blank"
              rel="noopener noreferrer"
              aria-label="View project on GitHub (opens in new tab)"
            >
              View on GitHub
            </a>
          </div>
        </section>

        {/* ── Features ────────────────────────────────── */}
        <section className="features-section" aria-labelledby="features-heading">
          <p id="features-heading" className="features-section-title">What Cerebro does</p>
          <div className="features-grid" role="list">
            {FEATURES.map((f) => (
              <article key={f.name} className="feature-card" role="listitem">
                <div className="feature-icon" aria-hidden="true">{f.icon}</div>
                <h2 className="feature-name">{f.name}</h2>
                <p className="feature-desc">{f.desc}</p>
              </article>
            ))}
          </div>
        </section>

        {/* ── Trust bar ───────────────────────────────── */}
        <div className="trust-bar" role="list" aria-label="Key properties">
          <span className="trust-item" role="listitem">
            <Shield size={14} aria-hidden="true" /> Strict user isolation
          </span>
          <span className="trust-item" role="listitem">
            <Zap size={14} aria-hidden="true" /> RRF hybrid retrieval
          </span>
          <span className="trust-item" role="listitem">
            <Lock size={14} aria-hidden="true" /> Bearer-token authenticated
          </span>
          <span className="trust-item" role="listitem">
            <Database size={14} aria-hidden="true" /> Versioned indexes
          </span>
        </div>

        <footer className="landing-footer">
          <p>
            &copy; {new Date().getFullYear()} Cerebro. Built for developers.
          </p>
        </footer>
      </div>

      {/* ── Auth modal ──────────────────────────────── */}
      {showAuth && (
        <div
          className="auth-overlay"
          role="presentation"
          aria-hidden={!showAuth}
          onClick={(e) => { if (e.target === e.currentTarget) closeAuth(); }}
        >
          <div
            ref={authRef}
            role="dialog"
            aria-modal="true"
            aria-labelledby="auth-dialog-title"
            style={{ width: '100%', maxWidth: '420px' }}
          >
            <Auth onClose={closeAuth} dialogTitleId="auth-dialog-title" />
          </div>
        </div>
      )}
    </>
  );
}
