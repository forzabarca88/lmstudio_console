/**
 * Chat functionality: send messages, handle streaming responses, render messages,
 * track metrics, file attachments, and agentic tool calls.
 */

import { state, saveSettings, saveCurrentSession, saveSessionHistory } from "./state.js";
import { apiCallStream, apiCall } from "./api.js";
import { showToast, scrollToBottom, autoResizeInput, updateMetrics } from "./ui.js";
import { renderHistoryList } from "./history.js";

// Initialize mermaid
try {
    mermaid.initialize({
        startOnLoad: false,
        theme: "dark",
        securityLevel: "loose",
    });
} catch (e) {
    // mermaid may not be loaded yet
}

/**
 * Start a new chat session (clear history).
 * @param {Object} dom - DOM element references.
 */
export function newChat(dom) {
    // Save current session before clearing
    if (state.chatMessages.length > 0) {
        saveCurrentSession();
        renderHistoryList(dom);
    }

    state.chatMessages = [];
    state.currentSessionId = null;
    state.streaming = false;
    state.attachments = [];
    state.metrics = { tokensPerSecond: 0, timeToFirstToken: null, totalTokens: 0 };
    dom.chatMessages.innerHTML = "";
    dom.streamingIndicator.style.display = "none";
    dom.chatMetrics.style.display = "none";
    updateMetrics(dom);
    dom.chatInput.disabled = !state.selectedModel;
    dom.sendBtn.disabled = !state.selectedModel;
    if (dom.attachBtn) dom.attachBtn.disabled = !state.selectedModel;
    clearAttachments(dom);
    showToast("Chat cleared", "info");
}

/**
 * Clear file attachments from the current session.
 * @param {Object} dom
 */
export function clearAttachments(dom) {
    state.attachments = [];
    if (dom.attachmentList) dom.attachmentList.innerHTML = "";
    if (dom.attachmentPreview) dom.attachmentPreview.style.display = "none";
}

/**
 * Render attachment preview chips.
 * @param {Object} dom
 */
export function renderAttachmentPreview(dom) {
    if (!dom.attachmentList || !dom.attachmentPreview) return;

    dom.attachmentList.innerHTML = "";

    if (state.attachments.length === 0) {
        dom.attachmentPreview.style.display = "none";
        return;
    }

    dom.attachmentPreview.style.display = "block";

    state.attachments.forEach((att, index) => {
        const chip = document.createElement("div");
        chip.className = "attachment-chip";

        if (att.preview) {
            const thumb = document.createElement("img");
            thumb.className = "attachment-thumb";
            thumb.src = att.preview;
            chip.appendChild(thumb);
        }

        const name = document.createElement("span");
        name.className = "attachment-name";
        name.textContent = att.name;
        chip.appendChild(name);

        const remove = document.createElement("button");
        remove.className = "attachment-remove";
        remove.textContent = "×";
        remove.title = "Remove";
        remove.addEventListener("click", (e) => {
            e.stopPropagation();
            window.removeAttachment(index);
        });
        chip.appendChild(remove);

        dom.attachmentList.appendChild(chip);
    });
}

/**
 * Upload a file to the backend for processing.
 * @param {File} file - File to upload
 * @returns {Promise<Object>} Upload result with base64 content
 */
async function uploadFile(file) {
    const formData = new FormData();
    formData.append("file", file);

    const response = await fetch("/api/upload", {
        method: "POST",
        body: formData,
    });

    if (!response.ok) {
        const error = await response.text();
        throw new Error(`Upload failed: ${error}`);
    }

    return response.json();
}

/**
 * Execute a tool call through the backend.
 * @param {string} toolName - Name of the tool
 * @param {Object} toolArgs - Tool arguments
 * @returns {Promise<string>} Tool result
 */
async function executeTool(toolName, toolArgs) {
    const response = await fetch("/api/tool-exec", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: toolName, arguments: toolArgs }),
    });

    if (!response.ok) {
        const error = await response.text();
        throw new Error(`Tool execution failed: ${error}`);
    }

    const data = await response.json();
    return data.result;
}

/**
 * Build a multimodal content array from text and attachments.
 * @param {string} text - User's text message
 * @param {Array} attachments - File attachments
 * @returns {Array|String} Content for the message
 */
function buildMultimodalContent(text, attachments) {
    if (attachments.length === 0) return text;

    const content = [];
    content.push({ type: "text", text });

    for (const att of attachments) {
        if (att.uploaded) {
            if (att.isImage) {
                content.push({
                    type: "image_url",
                    image_url: { url: `data:${att.mimeType};base64,${att.base64}` },
                });
            } else {
                // Non-image files: include as text with metadata
                content.push({
                    type: "text",
                    text: `[File: ${att.name} (${att.mimeType}, ${att.size} bytes)]\n${att.base64 ? "Content available" : ""}`,
                });
            }
        }
    }

    return content;
}

/**
 * Send a message and receive a streaming response.
 * Handles file attachments and agentic tool calls.
 * @param {Object} dom - DOM element references.
 */
export async function sendMessage(dom) {
    const text = dom.chatInput.value.trim();
    const hasAttachments = state.attachments.length > 0;

    if (!text && !hasAttachments) return;
    if (state.streaming || !state.selectedModel) return;

    state.streaming = true;
    dom.chatInput.disabled = true;
    dom.sendBtn.disabled = true;
    if (dom.attachBtn) dom.attachBtn.disabled = true;
    dom.chatInput.value = "";
    autoResizeInput(dom.chatInput);

    // Upload attachments to get base64 data
    const currentAttachments = [...state.attachments];
    for (const att of currentAttachments) {
        try {
            const result = await uploadFile(att.file);
            att.uploaded = true;
            att.base64 = result.base64;
            att.mimeType = result.mimeType;
            att.isImage = result.isImage;
        } catch (e) {
            showToast(`Failed to upload ${att.name}: ${e.message}`, "error");
        }
    }

    // Build multimodal content
    const userContent = buildMultimodalContent(text, currentAttachments);

    // Add user message
    const userMsg = { role: "user", content: userContent };
    state.chatMessages.push(userMsg);

    // Render user message with attachment indicators
    appendMessage(dom, userMsg, "user", currentAttachments);

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

    // Add tool definitions if enabled
    const body = {
        model: state.selectedModel,
        messages: messages,
        temperature: state.temperature,
        stream: true,
    };

    if (state.toolCallEnabled) {
        try {
            const tools = await apiCall("/api/tools");
            if (tools && tools.length > 0) {
                body.tools = tools;
                body.tool_choice = "auto";
            }
        } catch (e) {
            showToast(`Failed to load tool schemas: ${e.message}`, "error");
        }
    }

    // Clear attachments after sending
    clearAttachments(dom);

    try {
        const response = await apiCallStream("/v1/chat/completions", "POST", body);

        // Parse SSE stream
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        let assistantContent = "";
        let streamDone = false;
        let hasContent = false;
        let toolCalls = [];

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

                        // Check for error events
                        if (parsed.error) {
                            throw new Error(parsed.error.message || "Unknown error");
                        }
                        if (parsed.message?.error) {
                            throw new Error(parsed.message.error.message || parsed.message.message || "Unknown error");
                        }

                        const delta = parsed.choices?.[0]?.delta;

                        // Handle tool calls
                        if (delta?.tool_calls && state.toolCallEnabled) {
                            for (const tc of delta.tool_calls) {
                                const idx = tc.index;
                                if (!toolCalls[idx]) {
                                    toolCalls[idx] = { id: tc.id, type: "function", function: { name: tc.function?.name || "", arguments: "" } };
                                }
                                if (tc.function?.arguments) {
                                    toolCalls[idx].function.arguments += tc.function.arguments;
                                }
                            }
                        }

                        if (delta?.content) {
                            tokenCount++;
                            assistantContent += delta.content;
                            renderContent(contentEl, assistantContent);
                            scrollToBottom(dom.chatMessages);

                            if (!firstTokenTime) {
                                firstTokenTime = (Date.now() - streamStart) / 1000;
                            }

                            if (!hasContent) {
                                hasContent = true;
                                const textEl = document.getElementById("streamingIndicatorText");
                                if (textEl) textEl.textContent = "Generating...";
                            }

                            const elapsed = (Date.now() - streamStart) / 1000;
                            metrics.tokensPerSecond = elapsed > 0 ? tokenCount / elapsed : 0;
                            metrics.timeToFirstToken = firstTokenTime;
                            metrics.totalTokens = tokenCount;
                            updateMetrics(dom, metrics);
                        }

                        if (parsed.usage) {
                            metrics.totalTokens = parsed.usage.total_tokens || tokenCount;
                            updateMetrics(dom, metrics);
                        }
                    } catch (e) {
                        if (e.message && !e.message.includes("Unexpected token")) {
                            streamDone = true;
                            throw e;
                        }
                    }
                }
            }
        }

        // Hide streaming indicator
        if (dom.streamingIndicator) dom.streamingIndicator.style.display = "none";

        // Final metrics
        const totalDuration = (Date.now() - streamStart) / 1000;
        metrics.tokensPerSecond = totalDuration > 0 ? tokenCount / totalDuration : 0;
        metrics.timeToFirstToken = firstTokenTime || 0;
        metrics.totalTokens = tokenCount;
        updateMetrics(dom, metrics);

        // Save assistant message
        const assistantMsg = { role: "assistant", content: assistantContent };
        state.chatMessages.push(assistantMsg);
        state.metrics = { ...metrics };
        renderContent(contentEl, assistantContent);

        // Handle tool calls if present
        if (toolCalls.length > 0 && state.toolCallEnabled) {
            for (const tc of toolCalls) {
                if (!tc) continue;

                // Show tool call in chat
                const toolEl = appendToolCall(dom, tc, "executing");

                try {
                    // Execute the tool
                    const args = JSON.parse(tc.function.arguments || "{}");
                    const result = await executeTool(tc.function.name, args);

                    // Update tool call display
                    updateToolCallResult(toolEl, result);

                    // Add tool result to messages
                    const toolMsg = {
                        role: "tool",
                        content: result,
                        tool_call_id: tc.id,
                    };
                    state.chatMessages.push(toolMsg);

                    // Send follow-up request to continue the conversation
                    const followUpMessages = [];
                    if (state.systemPrompt.trim()) {
                        followUpMessages.push({ role: "system", content: state.systemPrompt });
                    }
                    followUpMessages.push(...state.chatMessages);

                    const followUpBody = {
                        model: state.selectedModel,
                        messages: followUpMessages,
                        temperature: state.temperature,
                        stream: true,
                    };
                    if (state.toolCallEnabled) {
                        const tools = await apiCall("/api/tools");
                        if (tools && tools.length > 0) {
                            followUpBody.tools = tools;
                            followUpBody.tool_choice = "auto";
                        }
                    }

                    // Create new assistant message for follow-up
                    const followUpEl = appendMessage(dom, { role: "assistant", content: "" }, "assistant");
                    const followUpContentEl = followUpEl.querySelector(".message-text");

                    const followUpResponse = await apiCallStream("/v1/chat/completions", "POST", followUpBody);
                    const followUpReader = followUpResponse.body.getReader();
                    const followUpDecoder = new TextDecoder();
                    let followUpBuffer = "";
                    let followUpContent = "";
                    let followUpDone = false;
                    let followUpToolCalls = [];

                    if (dom.streamingIndicator) {
                        dom.streamingIndicator.style.display = "flex";
                        const textEl = document.getElementById("streamingIndicatorText");
                        if (textEl) textEl.textContent = "Continuing...";
                    }

                    while (!followUpDone) {
                        const { done, value } = await followUpReader.read();
                        if (done) break;

                        followUpBuffer += followUpDecoder.decode(value, { stream: true });
                        const followUpLines = followUpBuffer.split("\n");
                        followUpBuffer = followUpLines.pop() || "";

                        for (const fLine of followUpLines) {
                            if (fLine.startsWith("data: ")) {
                                const fPayload = fLine.slice(6).trim();
                                if (fPayload === "[DONE]") {
                                    followUpDone = true;
                                    break;
                                }
                                try {
                                    const fParsed = JSON.parse(fPayload);
                                    if (fParsed.error) throw new Error(fParsed.error.message || "Unknown error");

                                    const fDelta = fParsed.choices?.[0]?.delta;

                                    if (fDelta?.tool_calls) {
                                        for (const ftc of fDelta.tool_calls) {
                                            const fIdx = ftc.index;
                                            if (!followUpToolCalls[fIdx]) {
                                                followUpToolCalls[fIdx] = { id: ftc.id, type: "function", function: { name: ftc.function?.name || "", arguments: "" } };
                                            }
                                            if (ftc.function?.arguments) {
                                                followUpToolCalls[fIdx].function.arguments += ftc.function.arguments;
                                            }
                                        }
                                    }

                                    if (fDelta?.content) {
                                        followUpContent += fDelta.content;
                                        renderContent(followUpContentEl, followUpContent);
                                        scrollToBottom(dom.chatMessages);
                                    }

                                    if (fParsed.usage) {
                                        metrics.totalTokens = fParsed.usage.total_tokens || metrics.totalTokens;
                                        updateMetrics(dom, metrics);
                                    }
                                } catch (fE) {
                                    if (fE.message && !fE.message.includes("Unexpected token")) {
                                        followUpDone = true;
                                        throw fE;
                                    }
                                }
                            }
                        }
                    }

                    if (dom.streamingIndicator) dom.streamingIndicator.style.display = "none";

                    const followUpMsg = { role: "assistant", content: followUpContent };
                    state.chatMessages.push(followUpMsg);
                    renderContent(followUpContentEl, followUpContent);

                    // If there are more tool calls, continue the loop
                    if (followUpToolCalls.length > 0) {
                        // For simplicity, stop after one round of tool calls
                        // to avoid infinite loops
                    }
                } catch (toolErr) {
                    updateToolCallResult(toolEl, `Error: ${toolErr.message}`);
                    state.chatMessages.push({
                        role: "tool",
                        content: `Error: ${toolErr.message}`,
                        tool_call_id: tc.id,
                    });
                }
            }
        }

        // Save session to history
        saveCurrentSession();
        renderHistoryList(dom);

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
    if (dom.attachBtn) dom.attachBtn.disabled = !state.selectedModel;
    dom.chatInput.focus();
}

/**
 * Append a tool call message to the chat.
 * @param {Object} dom
 * @param {Object} toolCall - { id, type, function: { name, arguments } }
 * @param {string} status - "executing" | "done"
 * @returns {HTMLElement}
 */
function appendToolCall(dom, toolCall, status) {
    const el = document.createElement("div");
    el.className = "tool-call";
    el.dataset.toolId = toolCall.id;

    const argsDisplay = toolCall.function.arguments || "{}";

    el.innerHTML = `
        <div class="message-avatar">🔧</div>
        <div class="tool-call-content">
            <div class="tool-call-header">
                <span class="tool-call-name">Tool: ${toolCall.function.name}</span>
                <span class="tool-call-status ${status}">${status === "executing" ? "Executing..." : "Done"}</span>
            </div>
            <div class="tool-call-args">${escapeHtml(argsDisplay)}</div>
            <div class="tool-call-result" style="display:none;"></div>
        </div>
    `;

    dom.chatMessages.appendChild(el);
    scrollToBottom(dom.chatMessages);
    return el;
}

/**
 * Update a tool call element with its result.
 * @param {HTMLElement} el - The tool call element
 * @param {string} result - Tool execution result
 */
function updateToolCallResult(el, result) {
    const statusEl = el.querySelector(".tool-call-status");
    const resultEl = el.querySelector(".tool-call-result");

    statusEl.className = "tool-call-status done";
    statusEl.textContent = "Done";

    resultEl.style.display = "block";
    resultEl.textContent = result;

    scrollToBottom(el.closest(".chat-messages") || document.getElementById("chatMessages"));
}

/**
 * Render content with markdown and mermaid support.
 * @param {HTMLElement} el - The element to render into
 * @param {string} content - Raw markdown content
 */
export async function renderContent(el, content) {
    // Check for mermaid code blocks
    const mermaidRegex = /```mermaid\s*([\s\S]*?)```/g;
    const hasMermaid = mermaidRegex.test(content);

    if (hasMermaid) {
        el.innerHTML = marked.parse(content);

        const preElements = el.querySelectorAll("pre");
        let mermaidId = 0;

        for (const pre of preElements) {
            const code = pre.querySelector("code");
            if (code && code.classList.contains("mermaid")) {
                const graphDef = code.textContent;
                const svgId = `mermaid-${mermaidId++}`;

                try {
                    const { svg } = await mermaid.render(svgId, graphDef);
                    pre.classList.add("mermaid-pre");
                    const svgDiv = document.createElement("div");
                    svgDiv.className = "mermaid";
                    svgDiv.innerHTML = svg;
                    pre.parentNode.insertBefore(svgDiv, pre.nextSibling);
                } catch (e) {
                    pre.classList.remove("mermaid-pre");
                }
            }
        }
    } else {
        el.innerHTML = marked.parse(content);
    }
}

/**
 * Append a message to the chat and render it.
 * @param {Object} dom
 * @param {Object} msg - { role, content }
 * @param {string} role - "user" | "assistant"
 * @param {Array} attachments - Optional file attachments for display
 * @returns {HTMLElement} The created message element.
 */
export function appendMessage(dom, msg, role, attachments = []) {
    const el = document.createElement("div");
    el.className = `message ${role}`;

    const avatar = role === "user" ? "U" : "AI";
    const roleLabel = role === "user" ? "You" : "Assistant";

    // Handle multimodal content
    let displayContent = "";
    if (typeof msg.content === "string") {
        displayContent = msg.content;
    } else if (Array.isArray(msg.content)) {
        displayContent = msg.content
            .filter(c => c.type === "text")
            .map(c => c.text)
            .join("\n");
    }

    let attachmentHtml = "";
    if (attachments.length > 0 && role === "user") {
        attachmentHtml = `<div class="message-attachments">`;
        for (const att of attachments) {
            if (att.preview) {
                attachmentHtml += `<img class="attachment-thumb-inline" src="${att.preview}" alt="${escapeHtml(att.name)}">`;
            } else {
                attachmentHtml += `<span class="attachment-icon">${escapeHtml(att.name)}</span>`;
            }
        }
        attachmentHtml += `</div>`;
    }

    el.innerHTML = `
        <div class="message-avatar">${avatar}</div>
        <div class="message-content">
            <div class="message-role">${roleLabel}</div>
            ${attachmentHtml}
            <div class="message-text"></div>
            ${role === "assistant" ? '<button class="copy-btn" title="Copy to clipboard">Copy</button>' : ''}
        </div>
    `;

    const contentEl = el.querySelector(".message-text");
    renderContent(contentEl, displayContent);

    // Copy button handler
    const copyBtn = el.querySelector(".copy-btn");
    if (copyBtn) {
        copyBtn.addEventListener("click", async (e) => {
            e.stopPropagation();
            const textToCopy = typeof msg.content === "string" ? msg.content :
                msg.content.filter(c => c.type === "text").map(c => c.text).join("\n");
            try {
                await navigator.clipboard.writeText(textToCopy);
                copyBtn.textContent = "Copied!";
                copyBtn.classList.add("copied");
                setTimeout(() => {
                    copyBtn.textContent = "Copy";
                    copyBtn.classList.remove("copied");
                }, 2000);
            } catch {
                const textarea = document.createElement("textarea");
                textarea.value = textToCopy;
                document.body.appendChild(textarea);
                textarea.select();
                document.execCommand("copy");
                document.body.removeChild(textarea);
                copyBtn.textContent = "Copied!";
                copyBtn.classList.add("copied");
                setTimeout(() => {
                    copyBtn.textContent = "Copy";
                    copyBtn.classList.remove("copied");
                }, 2000);
            }
        });
    }

    dom.chatMessages.appendChild(el);
    scrollToBottom(dom.chatMessages);
    return el;
}

/**
 * Escape HTML special characters.
 * @param {string} str
 * @returns {string}
 */
function escapeHtml(str) {
    const div = document.createElement("div");
    div.textContent = str;
    return div.innerHTML;
}
