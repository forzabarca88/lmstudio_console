/**
 * API call utilities - proxies requests through the backend.
 */

import { state } from "./state.js";

/**
 * Build common headers for API calls.
 * @returns {Object} Headers with auth and target URL.
 */
function _buildHeaders() {
    const headers = { "Content-Type": "application/json" };
    if (state.apiToken) {
        headers["Authorization"] = `Bearer ${state.apiToken}`;
    }
    if (state.endpoint) {
        headers["X-LM-Studio-URL"] = state.endpoint;
    }
    return headers;
}

/**
 * Make an API call through the proxy.
 * @param {string} path - API path (e.g. "/api/v1/models")
 * @param {string} method - HTTP method (default: "GET")
 * @param {Object|null} body - Request body (serialized as JSON)
 * @returns {Promise<Object>} Parsed JSON response
 */
export async function apiCall(path, method = "GET", body = null) {
    const headers = _buildHeaders();
    const options = { method, headers };
    if (body) options.body = JSON.stringify(body);

    const url = `/proxy${path}`;
    const response = await fetch(url, options);

    if (!response.ok) {
        const errorBody = await response.text();
        throw new Error(`API error ${response.status}: ${errorBody}`);
    }
    return response.json();
}

/**
 * Make a streaming API call through the proxy (returns the raw Response for SSE parsing).
 * Used for OpenAI-compatible streaming endpoints proxied through /proxy.
 * @param {string} path - API path
 * @param {string} method - HTTP method
 * @param {Object} body - Request body
 * @returns {Promise<Response>} Raw fetch Response
 */
export async function apiCallStream(path, method, body) {
    const headers = _buildHeaders();
    const url = `/proxy${path}`;
    const response = await fetch(url, {
        method,
        headers,
        body: JSON.stringify(body),
    });

    if (!response.ok) {
        const errorText = await response.text();
        throw new Error(`API error ${response.status}: ${errorText}`);
    }
    return response;
}

/**
 * Make a chat request to the Pydantic AI backend endpoint.
 * The backend handles tool calls automatically; frontend receives clean text stream.
 * @param {Object} body - Chat request body with model, messages, temperature, etc.
 * @returns {Promise<Response>} Raw fetch Response for SSE parsing
 */
export async function apiCallChat(body) {
    const headers = _buildHeaders();
    const response = await fetch("/api/chat", {
        method: "POST",
        headers,
        body: JSON.stringify(body),
    });

    if (!response.ok) {
        const errorText = await response.text();
        throw new Error(`Chat error ${response.status}: ${errorText}`);
    }
    return response;
}
