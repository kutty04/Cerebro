import React, { useState, useEffect, useRef } from 'react';
import ForceGraph2D from 'react-force-graph-2d';
import { Activity, Maximize2, Cpu } from 'lucide-react';
import { apiFetch } from '../apiClient';

export default function NeuralMap({ user }) {
  const [graphData, setGraphData] = useState({ nodes: [], links: [] });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const fgRef = useRef();

  const fetchGraphData = async () => {
    setLoading(true);
    setError('');
    try {
      const res = await apiFetch(`/graph-data?user_id=${user.id}`);
      if (!res.ok) {
        setError('Knowledge Map is unavailable in this backend version.');
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
      setError('Knowledge Map is unavailable in this version.');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchGraphData();
  }, [user.id]);

  useEffect(() => {
    // Zoom to fit after data loads
    if (!loading && fgRef.current && !error) {
        setTimeout(() => {
            fgRef.current.zoomToFit(400);
        }, 500);
    }
  }, [loading, error]);

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
            onClick={() => fgRef.current.zoomToFit(400)}
            className="ingest-nav-btn"
            style={{ height: 'fit-content' }}
          >
            <Maximize2 size={14} /> Center View
          </button>
        </div>
      </div>

      <div className="graph-wrapper" style={{ 
        flex: 1, 
        minHeight: '600px', 
        background: '#0a0f1d', // Solid background to block grid interference
        borderRadius: '24px', 
        border: '1px solid rgba(56, 189, 248, 0.1)', 
        position: 'relative',
        overflow: 'hidden',
        isolation: 'isolate' // Prevents CSS blend modes/filters from leaking in
      }}>
        <ForceGraph2D
          ref={fgRef}
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
            fgRef.current.centerAt(node.x, node.y, 1000);
            fgRef.current.zoom(2, 1000);
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
