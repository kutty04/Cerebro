import React, { useState, useEffect, useRef } from 'react';
import ForceGraph2D from 'react-force-graph-2d';
import { Activity, Maximize2 } from 'lucide-react';
import { apiFetch } from '../apiClient';

export default function NeuralMap({ user }) {
  const [graphData, setGraphData] = useState({ nodes: [], links: [] });
  const [loading, setLoading] = useState(true);
  const fgRef = useRef();

  const fetchGraphData = async () => {
    setLoading(true);
    try {
      const res = await apiFetch('/graph-data');
      if (!res.ok) throw new Error('Graph data endpoint error.');
      const data = await res.json();
      setGraphData(data);
    } catch (err) {
      console.error('Failed to fetch graph data:', err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchGraphData();
  }, [user.id]);

  useEffect(() => {
    if (!loading && fgRef.current) {
        setTimeout(() => {
            fgRef.current.zoomToFit(400);
        }, 500);
    }
  }, [loading]);

  if (loading) {
    return (
      <div className="loading-state" style={{ height: '500px', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        <Activity className="spin" /> Mapping Neural Network...
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
            onClick={() => fgRef.current && fgRef.current.zoomToFit(400)}
            className="ingest-nav-btn"
            style={{ height: 'fit-content' }}
          >
            <Maximize2 size={14} /> Center View
          </button>
        </div>
      </div>

      <div style={{ flex: 1, position: 'relative', background: 'rgba(15, 23, 42, 0.6)', borderRadius: '16px', border: '1px solid rgba(255,255,255,0.05)', overflow: 'hidden', minHeight: '450px' }}>
        <ForceGraph2D
          ref={fgRef}
          graphData={graphData}
          nodeLabel="name"
          nodeColor={node => node.color || '#38bdf8'}
          nodeVal={node => node.val || 5}
          linkColor={() => 'rgba(56, 189, 248, 0.2)'}
          linkWidth={1.5}
          backgroundColor="transparent"
          enableNodeDrag={true}
          enableZoom={true}
        />
      </div>
    </div>
  );
}
