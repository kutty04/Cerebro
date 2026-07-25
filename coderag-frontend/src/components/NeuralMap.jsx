import React, { useState, useEffect, useMemo } from 'react';
import { Table2, Share2, AlertTriangle, Filter, Search, X, Copy, Check, Folder, FileCode, Layers } from 'lucide-react';
import { fetchGraphData } from '../services';

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

  /* Filtered nodes */
  const filteredNodes = useMemo(() => {
    let nodes = graphData.nodes;

    /* Filter by selected repository */
    if (selectedRepo !== 'all') {
      nodes = nodes.filter(
        n => n.id === 'ME' || n.id === selectedRepo || n.group === selectedRepo
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
    }

    return nodes;
  }, [graphData, selectedRepo, filterQuery]);

  /* Process deterministic repository clusters with duplicate filename disambiguation */
  const repoClusters = useMemo(() => {
    if (filteredNodes.length === 0) return [];

    /* Identify duplicate filenames across all file nodes */
    const nameCounts = {};
    filteredNodes.forEach(n => {
      if (n.type === 'file') {
        const fn = n.name || n.id;
        nameCounts[fn] = (nameCounts[fn] || 0) + 1;
      }
    });

    /* Find all repository nodes or groups */
    const repos = filteredNodes.filter(n => n.type === 'repo');
    const fallbackGroups = new Set(
      filteredNodes
        .filter(n => n.type === 'file' && n.group)
        .map(n => n.group)
    );

    const repoNames = Array.from(new Set([
      ...repos.map(r => r.id),
      ...Array.from(fallbackGroups)
    ]));

    return repoNames.map((repoName) => {
      const repoNode = repos.find(r => r.id === repoName) || {
        id: repoName,
        name: repoName,
        type: 'repo',
        group: repoName,
        color: '#818cf8'
      };

      const files = filteredNodes
        .filter(n => n.type === 'file' && (n.group === repoName || n.id.startsWith(`${repoName}:`)))
        .map(fileNode => {
          const fn = fileNode.name || fileNode.id;
          let displayName = fn;
          if (nameCounts[fn] > 1 && fileNode.full_path) {
            const parts = fileNode.full_path.split('/');
            displayName = parts.length > 1 ? parts.slice(-2).join('/') : fileNode.full_path;
          }
          return {
            ...fileNode,
            displayName
          };
        });

      return {
        repoNode,
        files
      };
    });
  }, [filteredNodes]);

  /* Node click handler */
  const handleNodeClick = (node) => {
    setSelectedNode(node);
    const label = node.displayName || node.name || node.id;
    const typeStr = node.type === 'repo' ? 'repository' : node.type === 'core' ? 'Neural Core' : 'file';
    setAnnouncement(`Selected ${typeStr} node ${label}.`);
  };

  /* Copy file path helper */
  const handleCopyPath = (text) => {
    navigator.clipboard.writeText(text);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const isEmpty = !loading && !error && repoClusters.length === 0;

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
                setAnnouncement('Switched to deterministic SVG Knowledge Map view.');
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
                setAnnouncement(`Filtered map by repository: ${e.target.value}`);
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

      {/* Deterministic SVG Graph View */}
      {!loading && !error && !isEmpty && viewMode === 'graph' && (
        <div className="nm-svg-wrapper">
          <div className="nm-svg-container">
            {repoClusters.map((cluster) => (
              <SvgRepositoryCard
                key={cluster.repoNode.id}
                cluster={cluster}
                selectedNode={selectedNode}
                hoveredNode={hoveredNode}
                onNodeClick={handleNodeClick}
                onNodeHover={setHoveredNode}
              />
            ))}
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
              {filteredNodes.map((node, idx) => (
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
            Showing {filteredNodes.length} nodes
          </p>
        </div>
      )}
    </section>
  );
}

/* ─────────────────────────────────────────────────────────────
   Deterministic SVG Repository Cluster Component
   Renders repository hub and file nodes in a 100% zero-overlap grid
   ───────────────────────────────────────────────────────────── */
function SvgRepositoryCard({ cluster, selectedNode, hoveredNode, onNodeClick, onNodeHover }) {
  const { repoNode, files } = cluster;

  /* Layout Constants */
  const cols = Math.min(3, Math.max(1, Math.ceil(Math.sqrt(files.length))));
  const cardWidth = 720;
  const colWidth = Math.floor((cardWidth - 60) / cols);
  const rowHeight = 44;
  const headerHeight = 70;
  
  const numRows = Math.ceil(files.length / cols);
  const cardHeight = headerHeight + Math.max(1, numRows) * rowHeight + 30;

  const hubX = cardWidth / 2;
  const hubY = 36;

  return (
    <div className="svg-cluster-card" aria-label={`Repository cluster for ${repoNode.name}`}>
      <svg
        viewBox={`0 0 ${cardWidth} ${cardHeight}`}
        className="svg-cluster-canvas"
        width="100%"
        height={cardHeight}
        role="img"
        aria-label={`Knowledge map diagram for repository ${repoNode.name} containing ${files.length} indexed files`}
      >
        <defs>
          <linearGradient id={`grad-repo-${repoNode.id}`} x1="0%" y1="0%" x2="100%" y2="100%">
            <stop offset="0%" stopColor="#1e1b4b" stopOpacity="0.95" />
            <stop offset="100%" stopColor="#0f172a" stopOpacity="0.95" />
          </linearGradient>
        </defs>

        {/* Outer Card Background */}
        <rect
          x="2"
          y="2"
          width={cardWidth - 4}
          height={cardHeight - 4}
          rx="12"
          fill={`url(#grad-repo-${repoNode.id})`}
          stroke="rgba(129, 140, 248, 0.3)"
          strokeWidth="1.5"
        />

        {/* Empty Files Placeholder in Card */}
        {files.length === 0 && (
          <text
            x={hubX}
            y={headerHeight + 24}
            fill="#94a3b8"
            fontSize="12.5"
            fontFamily="Outfit, Inter, sans-serif"
            textAnchor="middle"
          >
            Repository registered • Indexed files will appear here
          </text>
        )}

        {/* File Node Grid & Connector Lines */}
        {files.map((file, idx) => {
          const col = idx % cols;
          const row = Math.floor(idx / cols);

          const fileX = 40 + col * colWidth + 12;
          const fileY = headerHeight + row * rowHeight + 20;

          const isSelected = selectedNode?.id === file.id;
          const isHovered = hoveredNode?.id === file.id;

          const labelText = file.displayName || file.name || file.id;
          const approxLabelWidth = Math.min(colWidth - 36, Math.max(60, labelText.length * 7.5));

          return (
            <g
              key={file.id}
              className={`svg-node-group ${isSelected ? 'selected' : ''} ${isHovered ? 'hovered' : ''}`}
              onClick={() => onNodeClick(file)}
              onMouseEnter={() => onNodeHover(file)}
              onMouseLeave={() => onNodeHover(null)}
              style={{ cursor: 'pointer' }}
              tabIndex={0}
              role="button"
              aria-label={`File node ${labelText} in ${repoNode.name}`}
              onKeyDown={(e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault();
                  onNodeClick(file);
                }
              }}
            >
              {/* Connector Link from Hub to File Node */}
              <path
                d={`M ${hubX} ${hubY + 16} Q ${hubX + (fileX - hubX) * 0.5} ${hubY + (fileY - hubY) * 0.5} ${fileX} ${fileY}`}
                fill="none"
                stroke={isSelected ? '#22d3ee' : isHovered ? '#38bdf8' : 'rgba(56, 189, 248, 0.25)'}
                strokeWidth={isSelected || isHovered ? 2 : 1}
                strokeDasharray={isSelected ? 'none' : '3 3'}
                aria-hidden="true"
              />

              {/* File Node Circle */}
              <circle
                cx={fileX}
                cy={fileY}
                r={isSelected ? 7 : isHovered ? 6 : 5}
                fill={isSelected ? '#22d3ee' : isHovered ? '#38bdf8' : '#94a3b8'}
                stroke={isSelected ? '#07090f' : '#1e293b'}
                strokeWidth="1.5"
              />

              {/* High Contrast Filename Label Pill Background */}
              <rect
                x={fileX + 10}
                y={fileY - 11}
                width={approxLabelWidth + 12}
                height="22"
                rx="4"
                fill={isSelected ? 'rgba(6, 182, 212, 0.95)' : isHovered ? 'rgba(15, 23, 42, 0.98)' : 'rgba(15, 23, 42, 0.92)'}
                stroke={isSelected ? '#22d3ee' : isHovered ? '#38bdf8' : 'rgba(56, 189, 248, 0.3)'}
                strokeWidth="1"
              />

              {/* Permanent Filename Label Text */}
              <text
                x={fileX + 16}
                y={fileY + 3}
                fill={isSelected ? '#07090f' : '#f8fafc'}
                fontSize="11.5"
                fontFamily="Outfit, Inter, sans-serif"
                fontWeight={isSelected ? '700' : '500'}
              >
                {labelText.length > 24 ? labelText.slice(0, 22) + '…' : labelText}
              </text>
            </g>
          );
        })}

        {/* Repository Header Hub (Rendered on top) */}
        <g
          className="svg-hub-group"
          onClick={() => onNodeClick(repoNode)}
          onMouseEnter={() => onNodeHover(repoNode)}
          onMouseLeave={() => onNodeHover(null)}
          style={{ cursor: 'pointer' }}
          role="button"
          tabIndex={0}
          aria-label={`Repository hub ${repoNode.name}`}
        >
          {/* Header Pill Badge */}
          <rect
            x={hubX - 140}
            y={hubY - 18}
            width="280"
            height="36"
            rx="18"
            fill="rgba(30, 27, 75, 0.95)"
            stroke="#818cf8"
            strokeWidth="2"
          />

          {/* Hub Icon / Circle */}
          <circle cx={hubX - 115} cy={hubY} r="8" fill="#818cf8" />

          {/* Hub Name Text */}
          <text
            x={hubX - 98}
            y={hubY + 5}
            fill="#ffffff"
            fontSize="14"
            fontFamily="Outfit, sans-serif"
            fontWeight="700"
            letterSpacing="0.05em"
          >
            {repoNode.name.length > 18 ? repoNode.name.slice(0, 16) + '…' : repoNode.name}
          </text>

          {/* File Count Badge */}
          <rect
            x={hubX + 80}
            y={hubY - 10}
            width="42"
            height="20"
            rx="10"
            fill="rgba(129, 140, 248, 0.25)"
          />
          <text
            x={hubX + 101}
            y={hubY + 4}
            fill="#818cf8"
            fontSize="10.5"
            fontFamily="Inter, sans-serif"
            fontWeight="700"
            textAnchor="middle"
          >
            {files.length} files
          </text>
        </g>
      </svg>
    </div>
  );
}
