import { useState, useEffect, useCallback } from 'react';
import { createRoot } from 'react-dom/client';
import { api } from '../lib/api';
import { getSecret, setSecret, getStorage, setStorage } from '../lib/storage';
import type { ExtensionStateResponse, Phase } from '../lib/types';

// ---------------------------------------------------------------------------
// Styles (inline — no Tailwind in popup)
// ---------------------------------------------------------------------------

const styles = {
  container: {
    width: 320,
    fontFamily: 'system-ui, -apple-system, sans-serif',
    fontSize: 13,
    padding: '12px 14px',
    color: '#1a1a1a',
    backgroundColor: '#fff',
  } satisfies React.CSSProperties,
  heading: {
    margin: '0 0 12px 0',
    fontSize: 15,
    fontWeight: 600,
  } satisfies React.CSSProperties,
  section: {
    marginBottom: 12,
  } satisfies React.CSSProperties,
  label: {
    display: 'block',
    marginBottom: 4,
    fontWeight: 500,
    color: '#555',
  } satisfies React.CSSProperties,
  row: {
    display: 'flex',
    gap: 6,
    alignItems: 'center',
  } satisfies React.CSSProperties,
  input: {
    flex: 1,
    padding: '4px 6px',
    border: '1px solid #ccc',
    borderRadius: 4,
    fontSize: 12,
    fontFamily: 'monospace',
  } satisfies React.CSSProperties,
  button: {
    padding: '4px 10px',
    border: '1px solid #0070f3',
    borderRadius: 4,
    backgroundColor: '#0070f3',
    color: '#fff',
    cursor: 'pointer',
    fontSize: 12,
  } satisfies React.CSSProperties,
  buttonSecondary: {
    padding: '4px 10px',
    border: '1px solid #999',
    borderRadius: 4,
    backgroundColor: '#f5f5f5',
    color: '#333',
    cursor: 'pointer',
    fontSize: 12,
  } satisfies React.CSSProperties,
  buttonDanger: {
    padding: '4px 10px',
    border: '1px solid #c00',
    borderRadius: 4,
    backgroundColor: '#fff0f0',
    color: '#c00',
    cursor: 'pointer',
    fontSize: 12,
  } satisfies React.CSSProperties,
  buttonDisabled: {
    padding: '4px 10px',
    border: '1px solid #ccc',
    borderRadius: 4,
    backgroundColor: '#f5f5f5',
    color: '#aaa',
    cursor: 'not-allowed',
    fontSize: 12,
    opacity: 0.6,
  } satisfies React.CSSProperties,
  statusBox: {
    border: '1px solid #e0e0e0',
    borderRadius: 4,
    padding: '8px 10px',
    backgroundColor: '#fafafa',
    marginBottom: 8,
  } satisfies React.CSSProperties,
  statusRow: {
    display: 'flex',
    justifyContent: 'space-between',
    marginBottom: 3,
  } satisfies React.CSSProperties,
  statusKey: {
    color: '#666',
  } satisfies React.CSSProperties,
  statusValue: {
    fontWeight: 500,
  } satisfies React.CSSProperties,
  error: {
    color: '#c00',
    marginTop: 4,
    fontSize: 12,
  } satisfies React.CSSProperties,
  saved: {
    color: '#080',
    marginTop: 4,
    fontSize: 12,
  } satisfies React.CSSProperties,
  info: {
    color: '#0070f3',
    marginTop: 4,
    fontSize: 12,
  } satisfies React.CSSProperties,
  divider: {
    border: 'none',
    borderTop: '1px solid #eee',
    margin: '10px 0',
  } satisfies React.CSSProperties,
};

const ACTIVE_PHASES: Phase[] = ['discovery_all', 'discovery_collections', 'enrichment', 'watch'];

// ---------------------------------------------------------------------------
// Popup component
// ---------------------------------------------------------------------------

function Popup() {
  const [secretInput, setSecretInput] = useState('');
  const [igUsernameInput, setIgUsernameInput] = useState('');
  const [secretSaved, setSecretSaved] = useState(false);
  const [secretError, setSecretError] = useState('');

  const [stateData, setStateData] = useState<ExtensionStateResponse | null>(null);
  const [stateError, setStateError] = useState('');
  const [stateLoading, setStateLoading] = useState(false);

  const [currentPhase, setCurrentPhase] = useState<Phase>('idle');
  const [discoveryMsg, setDiscoveryMsg] = useState('');

  // Load secret, ig_username, and current phase on mount
  useEffect(() => {
    getSecret().then((s) => setSecretInput(s ?? ''));
    getStorage(['ig_username']).then(({ ig_username }) => setIgUsernameInput(ig_username ?? ''));
    getStorage(['phase']).then(({ phase }) => setCurrentPhase(phase));
    loadState();
  }, []);

  const handleSaveSecret = async () => {
    setSecretSaved(false);
    setSecretError('');
    try {
      await setSecret(secretInput.trim());
      await setStorage({ ig_username: igUsernameInput.trim() });
      setSecretSaved(true);
      setTimeout(() => setSecretSaved(false), 2000);
    } catch (e) {
      setSecretError(String(e));
    }
  };

  const loadState = useCallback(async () => {
    setStateLoading(true);
    setStateError('');
    try {
      const data = await api.getState();
      setStateData(data);
    } catch (e) {
      setStateError(`Error: ${String(e)}`);
      setStateData(null);
    } finally {
      setStateLoading(false);
    }
  }, []);

  // Pause / Resume — also sends message to content scripts via background
  const handlePauseResume = async () => {
    if (currentPhase === 'paused') {
      // Resume
      await chrome.runtime.sendMessage({ type: 'resume' });
      const { phase } = await getStorage(['phase']);
      setCurrentPhase(phase);
    } else {
      // Pause
      await chrome.runtime.sendMessage({ type: 'pause' });
      await setStorage({ phase: 'paused' });
      setCurrentPhase('paused');
    }
  };

  // Start Discovery — transitions from idle to discovery_all
  const handleStartDiscovery = async () => {
    setDiscoveryMsg('');
    try {
      const reply = await chrome.runtime.sendMessage({ type: 'start_discovery' }) as
        | { ok: boolean; phase?: string; reason?: string }
        | undefined;
      if (reply?.ok) {
        setCurrentPhase('discovery_all');
        setDiscoveryMsg('Discovery started.');
        setTimeout(() => setDiscoveryMsg(''), 3000);
        // Refresh backend state
        await loadState();
      } else {
        setDiscoveryMsg(`Cannot start: ${reply?.reason ?? 'already active'}`);
      }
    } catch (e) {
      setDiscoveryMsg(`Error: ${String(e)}`);
    }
  };

  // Cancel Discovery — stop active discovery and return to idle
  const handleCancelDiscovery = async () => {
    await setStorage({ phase: 'idle' });
    setCurrentPhase('idle');
    setDiscoveryMsg('Discovery cancelled.');
    setTimeout(() => setDiscoveryMsg(''), 3000);
  };

  const formatDate = (iso: string | null | undefined): string => {
    if (!iso) return 'never';
    try {
      return new Date(iso).toLocaleString();
    } catch {
      return iso;
    }
  };

  const isActivePhase = ACTIVE_PHASES.includes(currentPhase);
  const isDiscovering =
    currentPhase === 'discovery_all' || currentPhase === 'discovery_collections';

  const canStartDiscovery = igUsernameInput.trim().length > 0;

  return (
    <div style={styles.container}>
      <h1 style={styles.heading}>instagram-logger</h1>

      {/* Instagram username entry */}
      <div style={styles.section}>
        <label style={styles.label}>Instagram username</label>
        <div style={styles.row}>
          <input
            type="text"
            style={styles.input}
            value={igUsernameInput}
            onChange={(e) => setIgUsernameInput(e.target.value)}
            placeholder="your_ig_username"
            onKeyDown={(e) => e.key === 'Enter' && handleSaveSecret()}
          />
        </div>
      </div>

      {/* Secret entry */}
      <div style={styles.section}>
        <label style={styles.label}>Ingest secret</label>
        <div style={styles.row}>
          <input
            type="password"
            style={styles.input}
            value={secretInput}
            onChange={(e) => setSecretInput(e.target.value)}
            placeholder="INGEST_SECRET from .env"
            onKeyDown={(e) => e.key === 'Enter' && handleSaveSecret()}
          />
          <button style={styles.button} onClick={handleSaveSecret}>
            Save
          </button>
        </div>
        {secretSaved && <div style={styles.saved}>Saved.</div>}
        {secretError && <div style={styles.error}>{secretError}</div>}
      </div>

      <hr style={styles.divider} />

      {/* Backend status */}
      <div style={styles.section}>
        <label style={styles.label}>Backend status</label>
        {stateLoading && <div style={{ color: '#888', fontSize: 12 }}>Loading…</div>}
        {stateError && <div style={styles.error}>{stateError}</div>}
        {stateData && !stateLoading && (
          <div style={styles.statusBox}>
            <div style={styles.statusRow}>
              <span style={styles.statusKey}>phase</span>
              <span style={styles.statusValue}>{stateData.phase_suggestion}</span>
            </div>
            <div style={styles.statusRow}>
              <span style={styles.statusKey}>posts known</span>
              <span style={styles.statusValue}>{stateData.total_discovered}</span>
            </div>
            <div style={styles.statusRow}>
              <span style={styles.statusKey}>enriched</span>
              <span style={styles.statusValue}>{stateData.total_enriched}</span>
            </div>
            <div style={styles.statusRow}>
              <span style={styles.statusKey}>lost</span>
              <span style={styles.statusValue}>{stateData.total_lost}</span>
            </div>
            <div style={styles.statusRow}>
              <span style={styles.statusKey}>placeholder</span>
              <span style={styles.statusValue}>{stateData.total_placeholder}</span>
            </div>
            <div style={styles.statusRow}>
              <span style={styles.statusKey}>last heartbeat</span>
              <span style={styles.statusValue}>
                {formatDate(stateData.last_heartbeat_at)}
              </span>
            </div>
          </div>
        )}
        <div style={styles.row}>
          <button style={styles.buttonSecondary} onClick={loadState}>
            Refresh
          </button>
        </div>
      </div>

      <hr style={styles.divider} />

      {/* Discovery controls */}
      <div style={styles.section}>
        <label style={styles.label}>Discovery</label>
        <div style={styles.row}>
          {currentPhase === 'idle' ? (
            <button
              style={canStartDiscovery ? styles.button : styles.buttonDisabled}
              onClick={canStartDiscovery ? handleStartDiscovery : undefined}
              disabled={!canStartDiscovery}
              title={canStartDiscovery ? undefined : 'Enter your Instagram username above first'}
            >
              Start Discovery
            </button>
          ) : isDiscovering ? (
            <button style={styles.buttonDanger} onClick={handleCancelDiscovery}>
              Cancel Discovery
            </button>
          ) : (
            <button style={styles.buttonDisabled} disabled>
              {currentPhase === 'paused' ? 'Paused' : `Active: ${currentPhase}`}
            </button>
          )}
          <span style={{ color: '#888', fontSize: 12 }}>local: {currentPhase}</span>
        </div>
        {discoveryMsg && <div style={styles.info}>{discoveryMsg}</div>}
      </div>

      <hr style={styles.divider} />

      {/* Pause / Resume */}
      <div style={styles.section}>
        <div style={styles.row}>
          {isActivePhase || currentPhase === 'paused' ? (
            <button style={styles.button} onClick={handlePauseResume}>
              {currentPhase === 'paused' ? 'Resume' : 'Pause'}
            </button>
          ) : (
            <button style={styles.buttonDisabled} disabled>
              {currentPhase === 'idle' ? 'Not running' : 'Pause'}
            </button>
          )}
          <span style={{ color: '#888', fontSize: 12 }}>
            local phase: {currentPhase}
          </span>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Mount
// ---------------------------------------------------------------------------

const root = document.getElementById('root');
if (root) {
  createRoot(root).render(<Popup />);
}
