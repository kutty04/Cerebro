import React, { useState, useEffect, useRef, lazy, Suspense } from 'react';
import { Maximize2, Table2, Share2, AlertTriangle } from 'lucide-react';
import { fetchGraphData } from '../services';

/* react-force-graph-2d is chunked separately */
const ForceGraph2D = lazy(() => import('react-force-graph-2d'));

export default function NeuralMap({ userId }) {
  const [graphData, setGraphData] = useState({ nodes: [], links: [] });
  const [loading, setLoading]     = useState(true);
  const [error, setError]         = useState(null);
  const [viewMode, setViewMode]   = useState('graph'); // 'graph' | 'table'
  const fgRef = useRef();

  useEffect(() => {
    setLoading(true);
    setError(null);
    fetchGraphData()
      .then((data) => {
        setGraphData(data);
        setLoading(false);
      })
      .catch((err) => {
        setError(err.message);
        setLoading(false);
      });
  }, [userId]);

  useEffect(() => {
    if (!loading && fgRef.current && viewMode === 'graph') {
      const t = setTimeout(() => {
        fgRef.current?.zoomToFit(400);
        
        // Push nodes much further apart (increase charge repulsion strength)
        fgRef.current.d3Force('charge')?.strength(-350);
        
        // Set link distance significantly wider
        fgRef.current.d3Force('link')?.distance(100);
        
        // Explicitly reheat simulation to compute new spaced positions
        fgRef.current.d3ReheatSimulation();
      }, 500);
      return () => clearTimeout(t);
    }
  }, [loading, viewMode, graphData]);

  const isEmpty = !loading && !error && graphData.nodes.length === 0;

  return (
    <section className="neural-map-section" aria-labelledby="neural-map-heading">
      {/* Header */}
      <div className="neural-map-header">
        <div>
          <h1 id="neural-map-heading" className="panel-title">Knowledge Map</h1>
          <p className="panel-subtitle">
            Repository-to-file index relationships for your indexed code.
            This graph shows which files belong to which indexed repository.
          </p>
        </div>
        <div className="neural-map-actions">
          {/* View toggle */}
          <div className="nm-view-toggle" role="group" aria-label="Map view mode">
            <button
              type="button"
              className={`nm-toggle-btn ${viewMode === 'graph' ? 'nm-toggle-btn--active' : ''}`}
              aria-pressed={viewMode === 'graph'}
              onClick={() => setViewMode('graph')}
              aria-label="Show graph view"
            >
              <Share2 size={14} aria-hidden="true" />
              Graph
            </button>
            <button
              type="button"
              className={`nm-toggle-btn ${viewMode === 'table' ? 'nm-toggle-btn--active' : ''}`}
              aria-pressed={viewMode === 'table'}
              onClick={() => setViewMode('table')}
              aria-label="Show table view (accessible fallback)"
            >
              <Table2 size={14} aria-hidden="true" />
              Table
            </button>
          </div>

          {viewMode === 'graph' && fgRef.current && (
            <button
              type="button"
              className="btn-ghost nm-center-btn"
              onClick={() => fgRef.current?.zoomToFit(400)}
              aria-label="Center and fit the graph to the viewport"
            >
              <Maximize2 size={14} aria-hidden="true" />
              Center view
            </button>
          )}
        </div>
      </div>

      {/* Loading */}
      {loading && (
        <div className="loading-panel" role="status" aria-live="polite">
          <div className="loading-spinner" aria-hidden="true" />
          <span>Loading knowledge map…</span>
        </div>
      )}

      {/* Error */}
      {!loading && error && (
        <div className="error-card" role="alert">
          <AlertTriangle size={18} aria-hidden="true" />
          <div>
            <p className="error-card-msg">{error}</p>
            <p className="error-card-hint">The knowledge map could not be loaded.</p>
          </div>
        </div>
      )}

      {/* Empty */}
      {isEmpty && (
        <div className="empty-state">
          <Share2 size={36} className="empty-icon" aria-hidden="true" />
          <h2 className="empty-title">No graph data available</h2>
          <p className="empty-desc">
            Index a repository first. Once indexed, the file-to-repository
            relationships will appear here.
          </p>
        </div>
      )}

      {/* Graph view */}
      {!loading && !error && !isEmpty && viewMode === 'graph' && (
        <div className="nm-graph-container" aria-label="Repository-to-file knowledge graph. Use Table view for an accessible alternative.">
          <Suspense fallback={
            <div className="loading-panel" role="status">
              <div className="loading-spinner" aria-hidden="true" />
              <span>Loading graph renderer…</span>
            </div>
          }>
            <ForceGraph2D
              ref={fgRef}
              graphData={graphData}
              nodeLabel="name"
              nodeCanvasObject={(node, ctx, globalScale) => {
                const label = node.name || '';
                const size = node.val || 5;
                
                // Draw node circle
                ctx.beginPath();
                ctx.arc(node.x, node.y, size, 0, 2 * Math.PI);
                ctx.fillStyle = node.color || '#38bdf8';
                ctx.fill();

                // Draw text labels with capped dynamic scaling
                const baseFontSize = 4;
                const labelFontSize = Math.max(3.0, Math.min(12.0, baseFontSize / globalScale));
                ctx.font = `${labelFontSize}px Outfit, Inter, sans-serif`;
                ctx.textAlign = 'center';
                ctx.textBaseline = 'middle';
                ctx.fillStyle = '#e2e8f0';

                // Display file names and repo names clearly
                ctx.fillText(label, node.x, node.y + size + labelFontSize + 1.0);
              }}
              nodePointerAreaPaint={(node, color, ctx) => {
                const size = node.val || 5;
                ctx.beginPath();
                ctx.arc(node.x, node.y, size, 0, 2 * Math.PI);
                ctx.fillStyle = color;
                ctx.fill();
              }}
              nodeColor={(node) => node.color || '#38bdf8'}
              nodeVal={(node) => node.val || 5}
              linkColor={() => 'rgba(56, 189, 248, 0.2)'}
              linkWidth={1.5}
              backgroundColor="transparent"
              enableNodeDrag={true}
              enableZoom={true}
            />
          </Suspense>
        </div>
      )}

      {/* Table view (accessible fallback) */}
      {!loading && !error && !isEmpty && viewMode === 'table' && (
        <div className="nm-table-wrapper">
          <table className="nm-table" aria-label="Repository-to-file index relationships">
            <caption className="nm-table-caption">
              Indexed files by repository. Each row represents an indexed file node.
            </caption>
            <thead>
              <tr>
                <th scope="col">File / Node</th>
                <th scope="col">Repository (group)</th>
                <th scope="col">Type</th>
              </tr>
            </thead>
            <tbody>
              {graphData.nodes.map((node, idx) => (
                <tr key={node.id ?? idx}>
                  <td className="nm-cell-name">{node.name ?? node.id ?? `Node ${idx + 1}`}</td>
                  <td className="nm-cell-group">{node.group ?? '—'}</td>
                  <td className="nm-cell-type">{node.type ?? 'file'}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="nm-table-summary">
            {graphData.nodes.length} nodes,{' '}
            {graphData.links.length} relationships
          </p>
        </div>
      )}
    </section>
  );
}
