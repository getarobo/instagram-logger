import type { ManifestV3Export } from '@crxjs/vite-plugin';

const manifest: ManifestV3Export = {
  manifest_version: 3,
  name: 'instagram-logger',
  version: '0.1.0',
  description:
    'Self-hosted Instagram saved-posts archiver. Backend: localhost:8000.',
  permissions: ['storage', 'alarms', 'offscreen', 'tabs', 'scripting'],
  host_permissions: [
    'https://www.instagram.com/*',
    'https://*.cdninstagram.com/*',
    'https://*.fbcdn.net/*',
    'http://127.0.0.1:8000/*',
  ],
  background: { service_worker: 'src/background.ts', type: 'module' },
  content_scripts: [
    {
      matches: ['https://www.instagram.com/*/saved/*'],
      js: ['src/content/saved-grid.ts'],
    },
    {
      matches: ['https://www.instagram.com/p/*'],
      js: ['src/content/post-detail.ts'],
    },
    {
      matches: ['https://www.instagram.com/*'],
      js: ['src/content/auth-watch.ts'],
      run_at: 'document_idle',
    },
  ],
  action: { default_popup: 'src/popup/popup.html' },
};

export default manifest;
