import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
export default defineConfig({
    base: './',
    plugins: [react()],
    build: {
        outDir: '../src/bulletjournal/_web',
        emptyOutDir: true,
        chunkSizeWarningLimit: 1100,
        rollupOptions: {
            output: {
                manualChunks: function (id) {
                    if (/\/node_modules\/(?:reactflow|@reactflow)\//.test(id)) {
                        return 'reactflow';
                    }
                    if (id.includes('/web/src/assets/')
                        || id.includes('/web/src/dashboard/')
                        || /\/node_modules\/(?:vega|vega-[^/]+|vega-lite|vega-embed)\//.test(id)) {
                        return 'dashboard';
                    }
                    return undefined;
                },
            },
        },
    },
    server: {
        host: '127.0.0.1',
        port: 5173,
    },
});
