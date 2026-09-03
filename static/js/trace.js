/**
 * Live trace log panel: SSE streaming from /api/trace-logs with real-time
 * log entries, auto-scroll, pause/resume, and exponential backoff reconnect.
 */

import { escapeHtml } from "./ui.js";

let eventSource = null;
let paused = false;
let reconnectDelay = 1000;
let reconnectTimer = null;

// CSS variables in the theme stylesheet handle restyling of existing
// entries automatically, so no DOM manipulation is needed.

/**
 * Open an SSE connection to /api/trace-logs and start streaming log entries.
 * Receives catch-up entries from the server buffer, then streams new entries.
 * Auto-scrolls to bottom on each entry (unless paused).
 * Reconnects with exponential backoff on disconnect (1s, 2s, 4s, 8s, max 30s).
 * @param {Object} dom - DOM element references including traceLog, tracePauseBtn.
 */
export function connectTraceLog(dom) {
    if (eventSource) return;
    reconnectDelay = 1000;
    startConnection(dom);
}

function startConnection(dom) {
    if (eventSource) return;

    eventSource = new EventSource("/api/trace-logs");

    eventSource.onopen = () => {
        reconnectDelay = 1000;
    };

    eventSource.onmessage = (event) => {
        reconnectDelay = 1000;
        try {
            const entry = JSON.parse(event.data);
            dom.traceLog.insertAdjacentHTML("beforeend", formatTraceEntry(entry));
            if (!paused) {
                dom.traceLog.scrollTop = dom.traceLog.scrollHeight;
            }
        } catch {
            // Ignore parse errors
        }
    };

    eventSource.onerror = () => {
        if (!eventSource) return;
        eventSource.close();
        eventSource = null;
        scheduleReconnect(dom);
    };
}

function scheduleReconnect(dom) {
    if (reconnectTimer) clearTimeout(reconnectTimer);
    reconnectTimer = setTimeout(() => {
        reconnectTimer = null;
        reconnectDelay = Math.min(reconnectDelay * 2, 30000);
        connectTraceLog(dom);
    }, reconnectDelay);
}

/**
 * Close the SSE connection and clear any pending reconnect timer.
 */
export function disconnectTraceLog() {
    if (reconnectTimer) {
        clearTimeout(reconnectTimer);
        reconnectTimer = null;
    }
    if (eventSource) {
        eventSource.close();
        eventSource = null;
    }
    reconnectDelay = 1000;
}

/**
 * Clear all entries from the trace log container.
 * @param {Object} dom
 */
export function clearTraceLog(dom) {
    dom.traceLog.innerHTML = "";
}

/**
 * Pause or resume auto-scroll. Updates button text between "Pause" and "Resume".
 * @param {Object} dom
 */
export function togglePause(dom) {
    paused = !paused;
    dom.tracePauseBtn.textContent = paused ? "Resume" : "Pause";
    if (!paused) {
        dom.traceLog.scrollTop = dom.traceLog.scrollHeight;
    }
}

/**
 * Format a log entry as an HTML string with timestamp, level badge, and message.
 * @param {Object} entry - {timestamp, level, message, ...}
 * @returns {string} HTML string for the trace entry.
 */
export function formatTraceEntry(entry) {
    const levelClass = (entry.level || "DEBUG").toLowerCase();
    const displayLevel = entry.level || "DEBUG";
    const timestamp = entry.timestamp || new Date().toLocaleTimeString("en-GB", { hour12: false });
    const message = escapeHtml(entry.message || "");

    return `<div class="trace-entry trace-${levelClass}">` +
        `<span class="trace-timestamp">${timestamp}</span>` +
        `<span class="trace-level">${displayLevel}</span>` +
        `<span class="trace-message">${message}</span></div>`;
}

