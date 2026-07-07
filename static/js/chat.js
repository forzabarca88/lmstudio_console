/**
 * Chat functionality: send messages, handle streaming responses, render messages, track metrics.
 */

import { state } from "./state.js";
import { apiCallStream } from "./api.js";
import { showToast, scrollToBottom, autoResizeInput, updateMetrics } from "./ui.js";

/**
 * Start a new chat session (clear history).
 * @param {Object} dom - DOM element references.
 */
export function newChat(dom) {
    state.chatMessages = [];
    state.streaming = false;
    state.metrics = { tokensPerSecond: 0, timeToFirstToken: null, totalTokens: 0 };
    dom.chatMessages.innerHTML = "";
    dom.streamingIndicator.style.display = "none";
    dom.chatMetrics.style.display = "none";
    updateMetrics(dom);
    dom.chatInput.disabled = !state.selectedModel;
    dom.sendBtn.disabled = !state.selectedModel;
    showToast("Chat cleared", "info");
}

/**
 * Send a message and receive a streaming response.
 * @param {Object} dom - DOM element references.
 */
export async function sendMessage(dom) {
    const text = dom.chatInput.value.trim();
    if (!text || state.streaming || !state.selectedModel) return;

    state.streaming = true;
    dom.chatInput.disabled = true;
    dom.sendBtn.disabled = true;
    dom.chatInput.value = "";
    autoResizeInput(dom.chatInput);

    // Add user message
    const userMsg = { role: "user", content: text };
    state.chatMessages.push(userMsg);
    appendMessage(dom, userMsg, "user");

    // Show streaming indicator and metrics
    if (dom.streamingIndicator) {
        dom.streamingIndicator.style.display = "flex";
        scrollToBottom(dom.chatMessages);
    }
    dom.chatMetrics.style.display = "flex";

    // Reset metrics for this turn
    const metrics = { tokensPerSecond: 0, timeToFirstToken: null, totalTokens: 0 };
    const streamStart = Date.now();
    let tokenCount = 0;
    let firstTokenTime = null;

    // Create assistant message placeholder
    const assistantEl = appendMessage(dom, { role: "assistant", content: "" }, "assistant");
    const contentEl = assistantEl.querySelector(".message-text");

    // Build messages array with system prompt
    const messages = [];
    if (state.systemPrompt.trim()) {
        messages.push({ role: "system", content: state.systemPrompt });
    }
    messages.push(...state.chatMessages);

    try {
        const response = await apiCallStream("/v1/chat/completions", "POST", {
            model: state.selectedModel,
            messages: messages,
            temperature: state.temperature,
            stream: true,
        });

        // Parse SSE stream
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        let assistantContent = "";
        let streamDone = false;
        let hasContent = false;

        while (!streamDone) {
            const { done, value } = await reader.read();
            if (done) break;

            buffer += decoder.decode(value, { stream: true });

            // Process complete lines
            const lines = buffer.split("\n");
            buffer = lines.pop() || "";

            for (const line of lines) {
                if (line.startsWith("data: ")) {
                    const payload = line.slice(6).trim();
                    if (payload === "[DONE]") {
                        streamDone = true;
                        break;
                    }
                    try {
                        const parsed = JSON.parse(payload);

                        // Check for error events (e.g. model crash)
                        if (parsed.error) {
                            throw new Error(parsed.error.message || "Unknown error");
                        }

                        if (parsed.message?.error) {
                            throw new Error(parsed.message.error.message || parsed.message.message || "Unknown error");
                        }

                        const delta = parsed.choices?.[0]?.delta?.content;
                        if (delta) {
                            tokenCount++;
                            assistantContent += delta;
                            contentEl.innerHTML = marked.parse(assistantContent);
                            scrollToBottom(dom.chatMessages);

                            // Track time to first token
                            if (!firstTokenTime) {
                                firstTokenTime = (Date.now() - streamStart) / 1000;
                            }

                            // Switch indicator text once real content arrives
                            if (!hasContent) {
                                hasContent = true;
                                const textEl = document.getElementById("streamingIndicatorText");
                                if (textEl) textEl.textContent = "Generating...";
                            }

                            // Update live metrics
                            const elapsed = (Date.now() - streamStart) / 1000;
                            metrics.tokensPerSecond = elapsed > 0 ? tokenCount / elapsed : 0;
                            metrics.timeToFirstToken = firstTokenTime;
                            metrics.totalTokens = tokenCount;
                            updateMetrics(dom, metrics);
                        }

                        // Check for usage stats in the final chunk
                        if (parsed.usage) {
                            metrics.totalTokens = parsed.usage.total_tokens || tokenCount;
                            updateMetrics(dom, metrics);
                        }
                    } catch (e) {
                        // If it's our thrown error, propagate it
                        if (e.message && !e.message.includes("Unexpected token")) {
                            streamDone = true;
                            throw e;
                        }
                        // Skip unparseable JSON lines
                    }
                } else if (line.startsWith("event: error")) {
                    // Mark that next data line is an error
                    // (handled in the data: block above)
                }
            }
        }

        // Hide indicator on normal completion
        if (dom.streamingIndicator) dom.streamingIndicator.style.display = "none";

        // Final metrics calculation
        const totalDuration = (Date.now() - streamStart) / 1000;
        metrics.tokensPerSecond = totalDuration > 0 ? tokenCount / totalDuration : 0;
        metrics.timeToFirstToken = firstTokenTime || 0;
        metrics.totalTokens = tokenCount;
        updateMetrics(dom, metrics);

        // Save final assistant message
        const assistantMsg = { role: "assistant", content: assistantContent };
        state.chatMessages.push(assistantMsg);
        state.metrics = { ...metrics };
        contentEl.innerHTML = marked.parse(assistantContent);

    } catch (e) {
        // Hide streaming indicator on error
        if (dom.streamingIndicator) dom.streamingIndicator.style.display = "none";

        // Remove the empty assistant placeholder and show error message
        assistantEl.remove();

        const errorEl = document.createElement("div");
        errorEl.className = "message assistant";
        errorEl.innerHTML = `
            <div class="message-avatar">AI</div>
            <div class="message-content">
                <div class="message-role">Assistant</div>
                <div class="error-message">Error: ${e.message}</div>
            </div>
        `;
        dom.chatMessages.appendChild(errorEl);
        scrollToBottom(dom.chatMessages);

        showToast(`Chat failed: ${e.message}`, "error");
    }

    state.streaming = false;
    dom.chatInput.disabled = false;
    dom.sendBtn.disabled = false;
    dom.chatInput.focus();
}

/**
 * Append a message to the chat and render it.
 * @param {Object} dom
 * @param {Object} msg - { role, content }
 * @param {string} role - "user" | "assistant"
 * @returns {HTMLElement} The created message element.
 */
export function appendMessage(dom, msg, role) {
    const el = document.createElement("div");
    el.className = `message ${role}`;

    const avatar = role === "user" ? "U" : "AI";
    const roleLabel = role === "user" ? "You" : "Assistant";

    el.innerHTML = `
        <div class="message-avatar">${avatar}</div>
        <div class="message-content">
            <div class="message-role">${roleLabel}</div>
            <div class="message-text">${msg.content ? marked.parse(msg.content) : ''}</div>
        </div>
    `;

    dom.chatMessages.appendChild(el);
    scrollToBottom(dom.chatMessages);
    return el;
}
