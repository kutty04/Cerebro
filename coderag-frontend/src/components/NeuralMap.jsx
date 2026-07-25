import React, { useState, useEffect, useRef, lazy, Suspense, useMemo } from 'react';
import { Maximize2, Table2, Share2, AlertTriangle, Eye, Filter, Search, X, Copy, Check } from 'lucide-react';
import { fetchGraphData } from '../services';

/* react-force-graph-2d is chunked separately */
const ForceGraph2D = lazy(() => import('react-force-graph-2d'));

export default function NeuralMap({ userId }) {
  const [graphData, setGraphData] = useState({ nodes: [], links: [] });
  const [loading, setLoading]     = useState(true);
  const [error, setError]         = useState(null);
  const [viewMode, setViewMode]   = useState('graph'); // 'graph' | 'table'
  
  /* Interactive state */
  const [selectedNode, setSelectedNode] = useState(null);
  const [hoveredNode, setHoveredNode]   = useState(null);
  const [selectedRepo, setSelectedRepo] = useState('all');
  const [filterQuery, setFilterQuery]   = useState('');
  const [labelMode, setLabelMode]       = useState('auto'); // 'auto' | 'always' | 'repos'
  const [announcement, setAnnouncement] = useState('');
  const [copied, setCopied]             = useState(false);
  
  const fgRef = useRef();

  /* Load graph data */
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

  /* Extract unique repositories for filter dropdown */
  const repoList = useMemo(() => {
    const repos = new Set();
    graphData.nodes.forEach(n => {
      if (n.type === 'repo' || (n.group && n.group !== 'Neural Core')) {
        repos.add(n.group || n.id);
      }
    });
    return Array.from(repos);
  }, [graphData]);

  /* Filtered nodes and links */
  const filteredData = useMemo(() => {
    let nodes = graphData.nodes;
    let links = graphData.links;

    /* Filter by selected repository */
    if (selectedRepo !== 'all') {
      nodes = nodes.filter(
        n => n.id === 'ME' || n.id === selectedRepo || n.group === selectedRepo
      );
      const nodeIds = new Set(nodes.map(n => n.id));
      links = links.filter(
        l => nodeIds.has(l.source.id ?? l.source) && nodeIds.has(l.target.id ?? l.target)
      );
    }

    /* Filter by text search query */
    if (filterQuery.trim()) {
      const q = filterQuery.toLowerCase().trim();
      const matchedNodeIds = new Set(
        nodes
          .filter(n => (n.name ?? '').toLowerCase().includes(q) || (n.full_path ?? n.id).toLowerCase().includes(q))
          .map(n => n.id)
      );
      matchedNodeIds.add('ME'); // keep core node
      // include repos connected to matched files
      nodes.forEach(n => {
        if (n.type === 'repo' && nodes.some(fn => fn.group === n.id && matchedNodeIds.has(fn.id))) {
          matchedNodeIds.add(n.id);
        }
      });
      nodes = nodes.filter(n => matchedNodeIds.has(n.id));
      const nodeIds = new Set(nodes.map(n => n.id));
      links = links.filter(
        l => nodeIds.has(l.source.id ?? l.source) && nodeIds.has(l.target.id ?? l.target)
      );
    }

    return { nodes, links };
  }, [graphData, selectedRepo, filterQuery]);

  /* Configure force layout simulation */
  useEffect(() => {
    if (!loading && fgRef.current && viewMode === 'graph') {
      const timer = setTimeout(() => {
        const fg = fgRef.current;
        if (!fg) return;

        // Custom D3 force distance and repulsion for clear label separation
        fg.d3Force('charge')?.strength(-360);
        fg.d3Force('link')?.distance(link => {
          const targetType = link.target?.type ?? '';
          if (targetType === 'repo') return 160;
          return 115;
        });

        fg.d3ReheatSimulation();
        fg.zoomToFit(400, 50);
      }, 200);

      return () => clearTimeout(timer);
    }
  }, [loading, viewMode, filteredData]);

  /* Center view handler */
  const handleCenterView = () => {
    if (fgRef.current) {
      fgRef.current.zoomToFit(400, 40);
      setAnnouncement('Graph view centered and fitted to viewport.');
    }
  };

  /* Node click handler */
  const handleNodeClick = (node) => {
    setSelectedNode(node);
    const label = node.name || node.id;
    const typeStr = node.type === 'repo' ? 'repository' : node.type === 'core' ? 'Neural Core' : 'file';
    setAnnouncement(`Selected ${typeStr} node ${label}.`);

    // If repository node clicked, focus or zoom to fit
    if (node.type === 'repo' && fgRef.current) {
      fgRef.current.centerAt(node.x, node.y, 400);
      fgRef.current.zoom(2.5, 400);
    }
  };

  /* Copy file path helper */
  const handleCopyPath = (text) => {
    navigator.clipboard.writeText(text);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const isEmpty = !loading && !error && filteredData.nodes.length === 0;
  const fileNodesCount = filteredData.nodes.filter(n => n.type === 'file').length;

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
              onClick={() => {
                setViewMode('graph');
                setAnnouncement('Switched to interactive Graph view.');
              }}
              aria-label="Show graph view"
            >
              <Share2 size={14} aria-hidden="true" />
              Graph
            </button>
            <button
              type="button"
              className={`nm-toggle-btn ${viewMode === 'table' ? 'nm-toggle-btn--active' : ''}`}
              aria-pressed={viewMode === 'table'}
              onClick={() => {
                setViewMode('table');
                setAnnouncement('Switched to accessible Table view.');
              }}
              aria-label="Show table view (accessible fallback)"
            >
              <Table2 size={14} aria-hidden="true" />
              Table
            </button>
          </div>

          {viewMode === 'graph' && (
            <button
              type="button"
              className="btn-ghost nm-center-btn"
              onClick={handleCenterView}
              aria-label="Center and fit graph to viewport"
            >
              <Maximize2 size={14} aria-hidden="true" />
              Center view
            </button>
          )}
        </div>
      </div>

      {/* Interactive Controls Bar */}
      {!loading && !error && graphData.nodes.length > 0 && (
        <div className="nm-controls-bar" role="toolbar" aria-label="Knowledge map graph controls">
          {/* Repository Scope Selector */}
          <div className="nm-control-group">
            <label htmlFor="nm-repo-select" className="nm-control-label">
              <Filter size={13} aria-hidden="true" />
              <span>Scope</span>
            </label>
            <select
              id="nm-repo-select"
              className="nm-select-input"
              value={selectedRepo}
              onChange={(e) => {
                setSelectedRepo(e.target.value);
                setSelectedNode(null);
                setAnnouncement(`Filtered graph by repository: ${e.target.value}`);
              }}
              aria-label="Filter graph by repository"
            >
              <option value="all">All Repositories ({repoList.length})</option>
              {repoList.map(repo => (
                <option key={repo} value={repo}>{repo}</option>
              ))}
            </select>
          </div>

          {/* Label Visibility Mode */}
          {viewMode === 'graph' && (
            <div className="nm-control-group">
              <label htmlFor="nm-label-select" className="nm-control-label">
                <Eye size={13} aria-hidden="true" />
                <span>Labels</span>
              </label>
              <select
                id="nm-label-select"
                className="nm-select-input"
                value={labelMode}
                onChange={(e) => setLabelMode(e.target.value)}
                aria-label="Set label visibility mode"
              >
                <option value="auto">Auto (Smart)</option>
                <option value="always">Always Show All</option>
                <option value="repos">Repos & Core Only</option>
              </select>
            </div>
          )}

          {/* Search/Filter Node Query */}
          <div className="nm-control-group nm-search-group">
            <label htmlFor="nm-search-input" className="sr-only">Search file nodes</label>
            <Search size={14} className="nm-search-icon" aria-hidden="true" />
            <input
              id="nm-search-input"
              type="search"
              className="nm-search-input"
              placeholder="Search file nodes..."
              value={filterQuery}
              onChange={(e) => setFilterQuery(e.target.value)}
              aria-label="Search nodes by filename"
            />
          </div>
        </div>
      )}

      {/* Screen Reader ARIA Live Region */}
      <div className="sr-only" aria-live="polite" aria-atomic="true">
        {announcement}
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
            {filterQuery || selectedRepo !== 'all'
              ? 'No nodes match the current filter or search criteria.'
              : 'Index a repository first. Once indexed, file-to-repository relationships will appear here.'
            }
          </p>
        </div>
      )}

      {/* Graph View */}
      {!loading && !error && !isEmpty && viewMode === 'graph' && (
        <div className="nm-graph-wrapper">
          <div
            className="nm-graph-container"
            aria-label="Repository-to-file knowledge graph canvas"
          >
            <Suspense fallback={
              <div className="loading-panel" role="status">
                <div className="loading-spinner" aria-hidden="true" />
                <span>Loading graph renderer…</span>
              </div>
            }>
              <ForceGraph2D
                ref={fgRef}
                graphData={filteredData}
                nodeColor={(node) => {
                  if (selectedNode?.id === node.id) return '#22d3ee';
                  if (node.type === 'core') return '#38bdf8';
                  if (node.type === 'repo') return '#818cf8';
                  return '#94a3b8';
                }}
                nodeVal={(node) => {
                  if (node.type === 'core') return 12;
                  if (node.type === 'repo') return 9;
                  return 5;
                }}
                linkColor={() => 'rgba(56, 189, 248, 0.25)'}
                linkWidth={1.5}
                backgroundColor="transparent"
                enableNodeDrag={true}
                enableZoom={true}
                onNodeClick={handleNodeClick}
                onNodeHover={(node) => setHoveredNode(node)}
                nodeCanvasObjectMode={() => 'after'}
                nodeCanvasObject={(node, ctx, globalScale) => {
                  const isCore = node.type === 'core' || node.id === 'ME';
                  const isRepo = node.type === 'repo';
                  const isSelected = selectedNode?.id === node.id;
                  const isHovered = hoveredNode?.id === node.id;

                  /* Permanently render labels for ALL nodes (file, repo, core) */
                  const showLabel = true;

                  /* Highlight ring on hover or selection */
                  if (isSelected || isHovered) {
                    ctx.beginPath();
                    ctx.arc(node.x, node.y, (node.val || 5) + 3, 0, 2 * Math.PI);
                    ctx.strokeStyle = isSelected ? '#22d3ee' : '#38bdf8';
                    ctx.lineWidth = 2 / (globalScale || 1);
                    ctx.stroke();
                  }

                  const label = node.name || node.id;
                  const fontSize = Math.max(10, Math.min(14, 12 / globalScale));
                  ctx.font = `${fontSize}px Outfit, Inter, sans-serif`;
                  ctx.textAlign = 'center';
                  ctx.textBaseline = 'top';

                  const textWidth = ctx.measureText(label).width;
                  const padX = 6 / globalScale;
                  const padY = 2 / globalScale;
                  const yOffset = (node.val || 5) + 4 / globalScale;

                  /* High contrast label pill background */
                  ctx.fillStyle = isSelected
                    ? 'rgba(6, 182, 212, 0.95)'
                    : isRepo
                    ? 'rgba(30, 27, 75, 0.9)'
                    : 'rgba(15, 23, 42, 0.88)';
                  
                  ctx.beginPath();
                  ctx.roundRect(
                    node.x - textWidth / 2 - padX,
                    node.y + yOffset - padY,
                    textWidth + padX * 2,
                    fontSize + padY * 2,
                    3 / globalScale
                  );
                  ctx.fill();
                  ctx.strokeStyle = isRepo ? '#818cf8' : 'rgba(56, 189, 248, 0.4)';
                  ctx.lineWidth = 0.8 / globalScale;
                  ctx.stroke();

                  /* Label text */
                  ctx.fillStyle = isSelected ? '#07090f' : '#f8fafc';
                  ctx.fillText(label, node.x, node.y + yOffset);
                }}
              />
            </Suspense>
          </div>

          {/* Node Details Side Panel */}
          {selectedNode && (
            <div
              className="nm-details-panel"
              role="region"
              aria-label={`Details for selected node ${selectedNode.name}`}
            >
              <div className="nm-details-header">
                <span className={`nm-node-badge badge-${selectedNode.type ?? 'file'}`}>
                  {selectedNode.type === 'repo' ? 'Repository' : selectedNode.type === 'core' ? 'Core' : 'File'}
                </span>
                <button
                  type="button"
                  className="btn-icon nm-details-close"
                  onClick={() => setSelectedNode(null)}
                  aria-label="Close node details panel"
                >
                  <X size={14} aria-hidden="true" />
                </button>
              </div>

              <h2 className="nm-details-title">{selectedNode.name}</h2>

              <div className="nm-details-body">
                {selectedNode.full_path && (
                  <div className="nm-details-field">
                    <span className="nm-field-label">Relative Path:</span>
                    <div className="nm-field-path-row">
                      <code className="nm-field-code">{selectedNode.full_path}</code>
                      <button
                        type="button"
                        className="copy-btn copy-btn-sm"
                        onClick={() => handleCopyPath(selectedNode.full_path)}
                        aria-label="Copy relative file path"
                      >
                        {copied ? <Check size={12} /> : <Copy size={12} />}
                      </button>
                    </div>
                  </div>
                )}

                {selectedNode.group && (
                  <div className="nm-details-field">
                    <span className="nm-field-label">Repository:</span>
                    <span className="nm-field-val">{selectedNode.group}</span>
                  </div>
                )}

                <div className="nm-details-field">
                  <span className="nm-field-label">Node ID:</span>
                  <span className="nm-field-val nm-field-id">{selectedNode.id}</span>
                </div>
              </div>
            </div>
          )}
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
                <th scope="col">Relative Path</th>
                <th scope="col">Repository</th>
                <th scope="col">Type</th>
              </tr>
            </thead>
            <tbody>
              {filteredData.nodes.map((node, idx) => (
                <tr key={node.id ?? idx}>
                  <td className="nm-cell-name">{node.name ?? node.id}</td>
                  <td className="nm-cell-path">
                    <code>{node.full_path ?? node.name ?? node.id}</code>
                  </td>
                  <td className="nm-cell-group">{node.group ?? '—'}</td>
                  <td className="nm-cell-type">{node.type ?? 'file'}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="nm-table-summary">
            Showing {filteredData.nodes.length} nodes and {filteredData.links.length} relationships
          </p>
        </div>
      )}
    </section>
  );
}
