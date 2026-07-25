import React, { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import { Maximize2, Table2, Share2, AlertTriangle } from 'lucide-react';
import { fetchGraphData } from '../services';

import ForceGraph2D from 'react-force-graph-2d';

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
      // Periodic check to capture async D3 force initialization and apply spacing
      let count = 0;
      const interval = setInterval(() => {
        const fg = fgRef.current;
        if (fg) {
          const charge = fg.d3Force('charge');
          const link = fg.d3Force('link');
          if (charge && link) {
            charge.strength(-400);
            link.distance(120);
            fg.d3ReheatSimulation();
            count++;
            if (count >= 6) {
              clearInterval(interval);
              fg.zoomToFit(400);
            }
          }
        }
      }, 300);

      return () => {
        clearInterval(interval);
      };
    }
  }, [loading, viewMode, graphData]);

  const drawNodeLabel = useCallback((node, ctx, globalScale) => {
    if (!node || typeof node.x !== 'number' || typeof node.y !== 'number') return;

    const label = node.name || '';
    const size = node.val || 5;
    const scale = globalScale || 1.0;
    
    // Draw text labels with scale-invariant rendering
    const labelFontSize = 10 / (scale > 0 ? scale : 1.0);
    ctx.font = `${labelFontSize}px Outfit, Inter, sans-serif`;
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';

    // 1. Draw a dark outline behind the text for high contrast
    ctx.strokeStyle = '#07090f';
    ctx.lineWidth = 3 / (scale > 0 ? scale : 1.0);
    ctx.strokeText(label, node.x, node.y + size + (labelFontSize * 0.7));

    // 2. Draw the actual light text
    ctx.fillStyle = '#e2e8f0';
    ctx.fillText(label, node.x, node.y + size + (labelFontSize * 0.7));
  }, []);

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
          <ForceGraph2D
            ref={fgRef}
            graphData={graphData}
            nodeLabel="name"
            nodeCanvasObjectMode="after"
            nodeCanvasObject={drawNodeLabel}
            nodeColor={(node) => node.color || '#38bdf8'}
            nodeVal={(node) => node.val || 5}
            linkColor={() => 'rgba(56, 189, 248, 0.2)'}
            linkWidth={1.5}
            backgroundColor="transparent"
            enableNodeDrag={true}
            enableZoom={true}
          />
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
