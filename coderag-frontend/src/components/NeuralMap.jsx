import React, { useState, useEffect, useRef, lazy, Suspense, useMemo } from 'react';
import { Maximize2, Table2, Share2, AlertTriangle, Filter, Search, X, Copy, Check } from 'lucide-react';
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

  /* Calculate dynamic cluster visual radius & deterministic constellation coordinates (fx, fy) */
  const layoutData = useMemo(() => {
    const rawNodes = filteredData.nodes.map(n => ({ ...n }));
    const links = filteredData.links;

    if (rawNodes.length === 0) return { nodes: [], links: [] };

    /* Identify duplicate filenames to render disambiguated relative paths */
    const nameCounts = {};
    rawNodes.forEach(n => {
      if (n.type === 'file') {
        const fn = n.name || n.id;
        nameCounts[fn] = (nameCounts[fn] || 0) + 1;
      }
    });

    rawNodes.forEach(n => {
      if (n.type === 'file') {
        const fn = n.name || n.id;
        if (nameCounts[fn] > 1 && n.full_path) {
          const parts = n.full_path.split('/');
          n.displayName = parts.length > 1 ? parts.slice(-2).join('/') : n.full_path;
        } else {
          n.displayName = fn;
        }
      } else {
        n.displayName = n.name || n.id;
      }
    });

    /* Find central core node */
    const coreNode = rawNodes.find(n => n.id === 'ME' || n.type === 'core');
    if (coreNode) {
      coreNode.fx = 0;
      coreNode.fy = 0;
    }

    /* Group repos and calculate dynamic visual radius for each cluster */
    const repoNodes = rawNodes.filter(n => n.type === 'repo');
    
    const repoClusters = repoNodes.map((repoNode) => {
      const repoFiles = rawNodes.filter(
        n => n.type === 'file' && (n.group === repoNode.id || n.group === repoNode.name)
      );

      /* Calculate ring count & outermost file ring radius */
      let numRings = 0;
      if (repoFiles.length > 0) {
        let remaining = repoFiles.length;
        let r = 1;
        while (remaining > 0) {
          const cap = Math.floor(8 + r * 6);
          remaining -= cap;
          numRings = r;
          r++;
        }
      }

      const outermostRingRadius = repoFiles.length === 0 ? 0 : 145 + (numRings - 1) * 115;
      
      /* Calculate maximum visible label half-width for this repository */
      let maxLabelHalfWidth = 35;
      repoFiles.forEach(f => {
        const labelText = f.displayName || f.name || f.id;
        const approxHalfWidth = Math.min(110, Math.max(30, (labelText.length * 7.5) / 2));
        if (approxHalfWidth > maxLabelHalfWidth) {
          maxLabelHalfWidth = approxHalfWidth;
        }
      });

      const nodeRadius = 5;
      const minOuterPadding = 25;

      /* Formula: clusterVisualRadius = outermostFileRingRadius + maximum visible label half-width + node radius + minimum outer padding */
      const visualRadius = outermostRingRadius + maxLabelHalfWidth + nodeRadius + minOuterPadding;

      return {
        repoNode,
        repoFiles,
        numRings,
        visualRadius,
      };
    });

    /* Calculate multi-repository center positions ensuring:
       distance >= clusterA.visualRadius + clusterB.visualRadius + 80px gap */
    const M = repoClusters.length;
    if (M === 1) {
      repoClusters[0].repoNode.fx = 0;
      repoClusters[0].repoNode.fy = 0;
    } else if (M > 1) {
      let maxRequiredSeparation = 0;
      for (let i = 0; i < M; i++) {
        const nextIdx = (i + 1) % M;
        const requiredSep = repoClusters[i].visualRadius + repoClusters[nextIdx].visualRadius + 80;
        if (requiredSep > maxRequiredSeparation) {
          maxRequiredSeparation = requiredSep;
        }
      }

      const hubRadius = Math.max(480, maxRequiredSeparation / (2 * Math.sin(Math.PI / M)));

      repoClusters.forEach((cluster, idx) => {
        const angle = (2 * Math.PI * idx) / M - Math.PI / 2;
        cluster.repoNode.fx = Math.round(hubRadius * Math.cos(angle));
        cluster.repoNode.fy = Math.round(hubRadius * Math.sin(angle));
      });
    }

    /* Position file nodes in concentric rings around their repository center */
    repoClusters.forEach((cluster) => {
      const repoX = cluster.repoNode.fx ?? 0;
      const repoY = cluster.repoNode.fy ?? 0;

      let currentRing = 1;
      let ringIndex = 0;

      cluster.repoFiles.forEach((fileNode) => {
        const ringRadius = 145 + (currentRing - 1) * 115;
        const ringCapacity = Math.floor(8 + currentRing * 6);

        const angleOffset = (currentRing % 2 === 0 ? 0.3 : 0);
        const angle = (2 * Math.PI * ringIndex) / ringCapacity + angleOffset - Math.PI / 2;

        fileNode.fx = Math.round(repoX + ringRadius * Math.cos(angle));
        fileNode.fy = Math.round(repoY + ringRadius * Math.sin(angle));

        ringIndex++;
        if (ringIndex >= ringCapacity) {
          currentRing++;
          ringIndex = 0;
        }
      });
    });

    /* Position standalone files if any */
    const standaloneFiles = rawNodes.filter(
      n => n.type === 'file' && (n.fx === undefined || n.fy === undefined)
    );
    standaloneFiles.forEach((fn, idx) => {
      const angle = (2 * Math.PI * idx) / (standaloneFiles.length || 1);
      fn.fx = Math.round(200 * Math.cos(angle));
      fn.fy = Math.round(200 * Math.sin(angle));
    });

    return { nodes: rawNodes, links };
  }, [filteredData]);

  /* Fit graph view after layout updates */
  useEffect(() => {
    if (!loading && fgRef.current && viewMode === 'graph') {
      const timer = setTimeout(() => {
        const fg = fgRef.current;
        if (!fg) return;
        fg.zoomToFit(400, 60);
      }, 150);

      return () => clearTimeout(timer);
    }
  }, [loading, viewMode, layoutData]);

  /* Center view handler */
  const handleCenterView = () => {
    if (fgRef.current) {
      fgRef.current.zoomToFit(400, 60);
      setAnnouncement('Graph view centered and fitted to viewport.');
    }
  };

  /* Node click handler */
  const handleNodeClick = (node) => {
    setSelectedNode(node);
    const label = node.displayName || node.name || node.id;
    const typeStr = node.type === 'repo' ? 'repository' : node.type === 'core' ? 'Neural Core' : 'file';
    setAnnouncement(`Selected ${typeStr} node ${label}.`);

    if (node.type === 'repo' && fgRef.current) {
      fgRef.current.centerAt(node.fx ?? node.x, node.fy ?? node.y, 400);
      fgRef.current.zoom(2.2, 400);
    }
  };

  /* Copy file path helper */
  const handleCopyPath = (text) => {
    navigator.clipboard.writeText(text);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const isEmpty = !loading && !error && layoutData.nodes.length === 0;

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
                graphData={layoutData}
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
                  const isRepo = node.type === 'repo';
                  const isSelected = selectedNode?.id === node.id;
                  const isHovered = hoveredNode?.id === node.id;
                  const scale = globalScale || 1.0;

                  /* Highlight ring on hover or selection */
                  if (isSelected || isHovered) {
                    ctx.beginPath();
                    ctx.arc(node.x, node.y, (node.val || 5) + 3, 0, 2 * Math.PI);
                    ctx.strokeStyle = isSelected ? '#22d3ee' : '#38bdf8';
                    ctx.lineWidth = 2 / scale;
                    ctx.stroke();
                  }

                  const label = node.displayName || node.name || node.id;
                  const fontSize = Math.max(10, Math.min(13, 11 / scale));
                  ctx.font = `${fontSize}px Outfit, Inter, sans-serif`;
                  ctx.textAlign = 'center';
                  ctx.textBaseline = 'top';

                  const textWidth = ctx.measureText(label).width;
                  const padX = 6 / scale;
                  const padY = 2 / scale;
                  const yOffset = (node.val || 5) + 4 / scale;

                  /* High contrast permanent label pill background */
                  ctx.fillStyle = isSelected
                    ? 'rgba(6, 182, 212, 0.95)'
                    : isRepo
                    ? 'rgba(30, 27, 75, 0.95)'
                    : 'rgba(15, 23, 42, 0.92)';
                  
                  ctx.beginPath();
                  ctx.roundRect(
                    node.x - textWidth / 2 - padX,
                    node.y + yOffset - padY,
                    textWidth + padX * 2,
                    fontSize + padY * 2,
                    3 / scale
                  );
                  ctx.fill();
                  ctx.strokeStyle = isSelected
                    ? '#22d3ee'
                    : isRepo
                    ? '#818cf8'
                    : 'rgba(56, 189, 248, 0.4)';
                  ctx.lineWidth = 0.8 / scale;
                  ctx.stroke();

                  /* Permanent white text label */
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
              aria-label={`Details for selected node ${selectedNode.displayName || selectedNode.name}`}
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

              <h2 className="nm-details-title">{selectedNode.displayName || selectedNode.name}</h2>

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
              {layoutData.nodes.map((node, idx) => (
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
            Showing {layoutData.nodes.length} nodes and {layoutData.links.length} relationships
          </p>
        </div>
      )}
    </section>
  );
}
