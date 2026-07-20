import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { VitePWA } from 'vite-plugin-pwa';

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [
    react(),
    VitePWA({
      registerType: 'autoUpdate',
      includeAssets: ['favicon.ico', 'apple-touch-icon.png', 'mask-icon.svg'],
      manifest: {
        name: 'Cerebro AI',
        short_name: 'Cerebro',
        description: 'Advanced CodeRAG Neural Engine',
        theme_color: '#0f172a',
        icons: [
          {
            src: 'pwa-192x192.png',
            sizes: '192x192',
            type: 'image/png'
          },
          {
            src: 'pwa-512x512.png',
            sizes: '512x512',
            type: 'image/png',
            purpose: 'any maskable'
          }
        ]
      },
      workbox: {
        globPatterns: ['**/*.{js,css,html,ico,png,svg,webmanifest}'],
        // Exclude ALL requests to the backend API origin from runtime caching.
        // A single origin-based rule is safer than maintaining a partial path
        // list: it covers every current and future backend route unconditionally,
        // including /search, /ingest, /index, /user-repos, /delete-repo,
        // /graph-data, /history, /analytics, /health, /readiness, /openapi.json,
        // /docs, and any route added in future phases.
        //
        // The backend origin is read from the VITE_API_URL build variable
        // (default: http://localhost:8000).  Any request whose hostname+port
        // matches that origin is handled NetworkOnly — the service worker
        // forwards it to the network and stores nothing.
        //
        // Supabase auth API calls (*.supabase.co) are also excluded because
        // they originate from a different host and therefore never match the
        // precache or runtimeCaching patterns.
        runtimeCaching: [
          {
            // Match every request to the configured backend API origin.
            // import.meta.env.VITE_API_URL is available at build time via Vite.
            urlPattern: ({ url }) => {
              const apiOrigin = (typeof VITE_API_URL !== 'undefined' ? VITE_API_URL : 'http://localhost:8000')
                .replace(/\/$/, '');
              try {
                const apiUrl = new URL(apiOrigin);
                return url.hostname === apiUrl.hostname && url.port === apiUrl.port;
              } catch {
                // Fallback: exclude anything that looks like a backend API path
                return url.pathname !== '/' && !url.pathname.match(/\.(js|css|html|ico|png|svg|webmanifest|woff2?|ttf|eot)$/i);
              }
            },
            handler: 'NetworkOnly',
          }
        ]
      }
    })
  ],
  server: {
    port: 3000,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, '')
      }
    }
  }
});
