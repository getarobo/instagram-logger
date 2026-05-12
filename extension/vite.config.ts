import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { crx } from '@crxjs/vite-plugin';
import manifest from './manifest.config';

export default defineConfig({
  plugins: [react(), crx({ manifest })],
  define: {
    __EXT_DEV__: JSON.stringify(process.env['EXT_DEV'] === '1'),
  },
});
