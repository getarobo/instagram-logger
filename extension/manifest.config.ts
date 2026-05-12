import type { ManifestV3Export } from '@crxjs/vite-plugin';

// EXT_DEV=1 env var enables localhost:9090 for fake-IG smoke testing.
// manifest.config.ts runs in Node context at vite build time.
// Access process.env via globalThis cast to avoid requiring @types/node.
const isDev =
  (
    (globalThis as unknown as { process?: { env?: Record<string, string> } })
      .process?.env?.['EXT_DEV']
  ) === '1';

const devHostPermissions = isDev
  ? ['http://localhost:9090/*', 'http://127.0.0.1:9090/*']
  : [];

const devContentScriptMatches = isDev
  ? [
      'http://localhost:9090/*/saved/*',
      'http://localhost:9090/*/saved/',
      'http://127.0.0.1:9090/*/saved/*',
      'http://127.0.0.1:9090/*/saved/',
    ]
  : [];

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
    ...devHostPermissions,
  ],
  background: { service_worker: 'src/background.ts', type: 'module' },
  content_scripts: [
    {
      matches: [
        'https://www.instagram.com/*/saved/*',
        ...devContentScriptMatches,
      ],
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
