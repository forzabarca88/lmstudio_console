/**
 * Utility functions: toast notifications, DOM helpers, formatting.
 */

import { state } from "./state.js";

/**
 * Show a toast notification.
 * @param {string} message - Notification text
 * @param {string} type - "success" | "error" | "info"
 */
export function showToast(message, type = "info") {
    const container = document.getElementById("toastContainer");
    const toast = document.createElement("div");
    toast.className = `toast ${type}`;
    toast.textContent = message;
    container.appendChild(toast);

    setTimeout(() => {
        toast.style.opacity = "0";
        toast.style.transform = "translateX(100%)";
        toast.style.transition = "all 0.2s";
        setTimeout(() => toast.remove(), 200);
    }, 3500);
}

/**
 * Format byte count to human-readable string.
 * @param {number} bytes
 * @returns {string}
 */
export function formatBytes(bytes) {
    if (bytes >= 1073741824) return (bytes / 1073741824).toFixed(1) + " GB";
    if (bytes >= 1048576) return (bytes / 1048576).toFixed(1) + " MB";
    if (bytes >= 1024) return (bytes / 1024).toFixed(1) + " KB";
    return bytes + " B";
}

/**
 * Escape HTML special characters, including quotes, so the result is safe
 * in both element text content and HTML attribute contexts.
 * @param {*} value - Value to escape (coerced to string).
 * @returns {string}
 */
export function escapeHtml(value) {
    return String(value)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#39;");
}

/**
 * Auto-resize textarea to fit content.
 * @param {HTMLTextAreaElement} el
 */
export function autoResizeInput(el) {
    el.style.height = "auto";
    el.style.height = Math.min(el.scrollHeight, 160) + "px";
}

/**
 * Scroll a container to its bottom.
 * @param {HTMLElement} el
 */
export function scrollToBottom(el) {
    el.scrollTop = el.scrollHeight;
}

/**
 * Update the chat metrics display.
 * @param {Object} dom - DOM element references.
 * @param {Object} metrics - { tokensPerSecond, timeToFirstToken, totalTokens }
 */
export function updateMetrics(dom, metrics = null) {
    if (!dom.metricTpsValue || !dom.metricTtftValue || !dom.metricTokensValue) return;

    const m = metrics || state.metrics;

    dom.metricTpsValue.textContent = m.tokensPerSecond > 0 ? m.tokensPerSecond.toFixed(1) : "0";
    dom.metricTtftValue.textContent = m.timeToFirstToken !== null ? m.timeToFirstToken.toFixed(2) + "s" : "—";
    dom.metricTokensValue.textContent = m.totalTokens.toString();
}
