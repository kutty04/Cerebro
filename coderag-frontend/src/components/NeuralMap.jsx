import React, { useState, useEffect, useRef } from 'react';
import ForceGraph2D from 'react-force-graph-2d';
import { Activity, Maximize2, Cpu } from 'lucide-react';
import { apiFetch } from '../apiClient';

export default function NeuralMap({ user, repoFilter = '', repositoryId = '' }) {
  const [graphData, setGraphData] = useState({ nodes: [], links: [] });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const fgRef = useRef();
  const containerRef = useRef();
  const [dimensions, setDimensions] = useState({ width: 0, height: 0 });

  const fetchGraphData = async () => {
    setLoading(true);
    setError('');
    try {
      let queryUrl = `/graph-data?user_id=${user.id}`;
      if (repositoryId) {
        queryUrl += `&repository_id=${encodeURIComponent(repositoryId)}`;
      } else if (repoFilter) {
        queryUrl += `&repo_name=${encodeURIComponent(repoFilter)}`;
      }

      const res = await apiFetch(queryUrl);
      if (!res.ok) {
        setError('Unable to load Neural Map for the selected repository.');
        return;
      }
      const data = await res.json();
      if (Array.isArray(data?.nodes) && Array.isArray(data?.links)) {
        setGraphData(data);
      } else {
        setGraphData({ nodes: [], links: [] });
      }
    } catch (err) {
      console.warn('Failed to fetch graph data:', err);
      setError('Failed to load Knowledge Map for the selected repository.');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchGraphData();
  }, [user.id, repoFilter, repositoryId]);

  // Dynamically measure container dimensions on tab activation, window resize, or container size changes
  useEffect(() => {
    if (!containerRef.current) return;

    const updateDimensions = () => {
      if (containerRef.current) {
        const { clientWidth, clientHeight } = containerRef.current;
        if (clientWidth > 0 && clientHeight > 0) {
          setDimensions({ width: clientWidth, height: clientHeight });
        }
      }
    };

    updateDimensions();

    const resizeObserver = new ResizeObserver(() => {
      updateDimensions();
    });
    resizeObserver.observe(containerRef.current);

    window.addEventListener('resize', updateDimensions);

    return () => {
      resizeObserver.disconnect();
      window.removeEventListener('resize', updateDimensions);
    };
  }, [loading, error]);

  const hasAutoFitRef = useRef(false);

  useEffect(() => {
    hasAutoFitRef.current = false;
  }, [graphData]);

  useEffect(() => {
    if (
      loading ||
      error ||
      hasAutoFitRef.current ||
      !fgRef.current ||
      dimensions.width <= 0 ||
      dimensions.height <= 0
    ) {
      return;
    }

    const timer = setTimeout(() => {
      fgRef.current?.zoomToFit(400, 50);
      hasAutoFitRef.current = true;
    }, 300);

    return () => clearTimeout(timer);
  }, [loading, error, graphData, dimensions.width, dimensions.height]);

  if (loading) {
    return (
      <div className="loading-state" style={{ height: '500px', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        <Activity className="spin" /> Mapping Neural Network...
      </div>
    );
  }

  if (error) {
    return (
      <div className="neural-map-container" style={{ padding: '3rem', textAlign: 'center' }}>
        <div className="empty-state" style={{ background: 'rgba(15, 23, 42, 0.6)', borderRadius: '24px', padding: '3rem', border: '1px solid rgba(56, 189, 248, 0.1)' }}>
          <Cpu size={48} className="neon-icon" style={{ marginBottom: '1rem', opacity: 0.7 }} />
          <h3>Knowledge Map Notice</h3>
          <p style={{ color: '#94a3b8', marginTop: '0.5rem' }}>{error}</p>
        </div>
      </div>
    );
  }

  return (
    <div className="neural-map-container" style={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
      <div className="view-header">
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <div>
            <h2>Neural Map</h2>
            <p>A semantic visualization of your indexed knowledge graph.</p>
          </div>
          <button 
            onClick={() => {
              if (fgRef.current) {
                fgRef.current.zoomToFit(400, 50);
              }
            }}
            className="ingest-nav-btn"
            style={{ height: 'fit-content' }}
          >
            <Maximize2 size={14} /> Center View
          </button>
        </div>
      </div>

      <div 
        ref={containerRef}
        className="graph-wrapper" 
        style={{ 
          flex: 1, 
          minHeight: '600px', 
          background: '#0a0f1d', // Solid background to block grid interference
          borderRadius: '24px', 
          border: '1px solid rgba(56, 189, 248, 0.1)', 
          position: 'relative',
          overflow: 'hidden',
          isolation: 'isolate' // Prevents CSS blend modes/filters from leaking in
        }}
      >
        {dimensions.width > 0 && dimensions.height > 0 && (
          <ForceGraph2D
            ref={fgRef}
            width={dimensions.width}
            height={dimensions.height}
            graphData={graphData}
            nodeLabel="name"
            nodeColor={node => node.color}
            nodeVal={node => node.val}
            linkColor={() => 'rgba(56, 189, 248, 0.15)'}
            linkWidth={1}
            backgroundColor="rgba(0,0,0,0)"
            d3AlphaDecay={0.01}
            d3VelocityDecay={0.1}
            cooldownTicks={200}
            onEngineStop={() => {
              if (fgRef.current) fgRef.current.zoomToFit(400, 50);
            }}
            onNodeClick={node => {
              if (fgRef.current) {
                fgRef.current.centerAt(node.x, node.y, 1000);
                fgRef.current.zoom(2, 1000);
              }
            }}
            nodeCanvasObject={(node, ctx, globalScale) => {
              const label = node.name;
              const fontSize = 12/globalScale;
              ctx.font = `${fontSize}px Inter`;
              const textWidth = ctx.measureText(label).width;

              ctx.fillStyle = node.color;
              ctx.beginPath(); 
              ctx.arc(node.x, node.y, node.val, 0, 2 * Math.PI, false);
              ctx.fill();

              // Glow effect for nodes
              ctx.shadowBlur = 15;
              ctx.shadowColor = node.color;

              // Only show labels when zoomed in or for important nodes
              if (globalScale > 1.2 || node.id === 'ME' || node.val > 8) {
                  ctx.fillStyle = 'rgba(255, 255, 255, 0.8)';
                  ctx.fillText(label, node.x - textWidth / 2, node.y + node.val + fontSize + 2);
              }
            }}
          />
        )}
      </div>
      
      <div className="graph-legend" style={{ display: 'flex', gap: '1.5rem', marginTop: '1rem', color: '#94a3b8', fontSize: '0.8rem' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
          <div style={{ width: '10px', height: '10px', borderRadius: '50%', background: '#38bdf8' }}></div> Neural Core
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
          <div style={{ width: '10px', height: '10px', borderRadius: '50%', background: '#818cf8' }}></div> Repository
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
          <div style={{ width: '10px', height: '10px', borderRadius: '50%', background: '#94a3b8' }}></div> Code Node
        </div>
      </div>
    </div>
  );
}

