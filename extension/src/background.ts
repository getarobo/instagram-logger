// Service worker entry — phase machine (idle only for E2).
// E3+ will add: chrome.tabs.onCreated/onRemoved, content-script messaging, etc.
//
// Phase machine: plan §4.2
// Tab ownership: consensus Δ4 — NEVER chrome.tabs.query against IG at large.

import { api } from './lib/api';
import { getStorage, setStorage, initStorageDefaults } from './lib/storage';

const ALARM_HEARTBEAT = 'heartbeat';

// ---------------------------------------------------------------------------
// Install / startup
// ---------------------------------------------------------------------------

chrome.runtime.onInstalled.addListener(async (details) => {
  if (details.reason === 'install') {
    // Initialize all storage keys to their defaults on first install.
    await initStorageDefaults();
  }

  // (Re-)create the heartbeat alarm on install and update.
  chrome.alarms.create(ALARM_HEARTBEAT, { periodInMinutes: 5 });
});

// Also recreate the alarm on SW startup (SW can be evicted and restarted).
chrome.alarms.create(ALARM_HEARTBEAT, {
  delayInMinutes: 0.1,
  periodInMinutes: 5,
});

// ---------------------------------------------------------------------------
// Alarm handler
// ---------------------------------------------------------------------------

chrome.alarms.onAlarm.addListener(async (alarm) => {
  if (alarm.name === ALARM_HEARTBEAT) {
    await handleHeartbeatAlarm();
  }
});

async function handleHeartbeatAlarm(): Promise<void> {
  try {
    const state = await api.getState();

    // Phase machine (idle only for E2):
    // Adopt the backend's phase suggestion if we're currently idle.
    const { phase } = await getStorage(['phase']);
    if (phase === 'idle' && state.phase_suggestion !== 'idle') {
      await setStorage({ phase: state.phase_suggestion });
    }

    // Persist the backend-reported phase suggestion for popup display.
    // Background wins on phase only when local is idle; otherwise local phase
    // (paused, etc.) takes precedence.
    // E3+ will drive actual phase transitions.
  } catch (e) {
    // Secret not configured yet, or backend unreachable. Log and continue.
    console.warn('[instagram-logger] heartbeat poll failed:', e);
  }
}

// ---------------------------------------------------------------------------
// Message listener (placeholder for E3+ content-script comms)
// ---------------------------------------------------------------------------

chrome.runtime.onMessage.addListener(
  (
    _message: unknown,
    _sender: chrome.runtime.MessageSender,
    _sendResponse: (response?: unknown) => void,
  ) => {
    // E3+ will handle messages from content scripts here.
    return false;
  },
);
