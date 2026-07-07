/**
 * API call utilities - proxies requests through the backend.
 */

import { state } from "./state.js";

/**
 * Make an API call through the proxy.
 * @param {string} path - API path (e.g. "/api/v1/models")
 * @param {string} method - HTTP method (default: "GET")
 * @param {Object|null} body - Request body (serialized as JSON)
 * @returns {Promise<Object>} Parsed JSON response
 */
export async function apiCall(path, method = "GET", body = null) {
    const headers = { "Content-Type": "application/json" };
    if (state.apiToken) {
        headers["Authorization"] = `Bearer ${state.apiToken}`;
    }
    if (state.endpoint) {
        headers["X-LM-Studio-URL"] = state.endpoint;
    }

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
 * Make a streaming API call (returns the raw Response for SSE parsing).
 * @param {string} path - API path
 * @param {string} method - HTTP method
 * @param {Object} body - Request body
 * @returns {Promise<Response>} Raw fetch Response
 */
export async function apiCallStream(path, method, body) {
    const headers = { "Content-Type": "application/json" };
    if (state.apiToken) {
        headers["Authorization"] = `Bearer ${state.apiToken}`;
    }
    if (state.endpoint) {
        headers["X-LM-Studio-URL"] = state.endpoint;
    }

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
