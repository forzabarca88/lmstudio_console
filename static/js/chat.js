/**
 * Chat functionality: send messages, handle streaming responses, render messages,
 * track metrics, file attachments.
 *
 * Tool calls are handled entirely by Pydantic AI on the backend.
 * The frontend only sends chat requests and receives clean text streams.
 */

import { state, saveSettings, saveCurrentSession, saveSessionHistory } from "./state.js";
import { apiCallChat, apiCall } from "./api.js";
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
        remove.textContent = "\u00d7";
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
                    text: `[File: ${att.name} (${att.mimeType}, ${att.size} bytes)]`,
                });
            }
        }
    }

    return content;
}

/**
 * Send a message and receive a streaming response via Pydantic AI.
 * The backend handles tool calls automatically.
 * @param {Object} dom - DOM element references.
 */
export async function sendMessage(dom) {
    const text = dom.chatInput.value.trim();
    const hasAttachments = state.attachments.length > 0;

    if (!text && !hasAttachments) return;
    if (state.streaming || !state.selectedModel) return;

    // Atomic guard: set immediately and disable input to prevent race
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

    // Reset metrics for this turn
    const metrics = { tokensPerSecond: 0, timeToFirstToken: null, totalTokens: 0 };
    const streamStart = Date.now();
    let tokenCount = 0;
    let firstTokenTime = null;

    // Thinking token tracking
    let thinkingContent = "";
    let thinkingDone = false;
    let thinkingWrapper = null; // outer <div class="thinking-block">
    let thinkingEl = null;      // inner <details>
    let thinkingContentEl = null;
    let thinkingSummaryEl = null;
    let thinkingSeen = false;   // tracks if any thinking events arrived

    // Assistant message placeholder
    let assistantEl = null;
    let contentEl = null;

    // Create assistant placeholder immediately so content events have somewhere to render
    createAssistantPlaceholder();

    // Show streaming indicator with "Thinking..." text
    if (dom.streamingIndicator) {
        dom.streamingIndicator.style.display = "flex";
        if (dom.streamingIndicatorText) {
            dom.streamingIndicatorText.textContent = "Generating...";
        }
        scrollToBottom(dom.chatMessages);
    }
    dom.chatMetrics.style.display = "flex";

    /**
     * Create the assistant message placeholder.
     */
    function createAssistantPlaceholder() {
        assistantEl = appendMessage(dom, { role: "assistant", content: "" }, "assistant");
        dom.chatMessages.appendChild(assistantEl);
        contentEl = assistantEl.querySelector(".message-text");
    }

    /**
     * Create thinking block lazily - only when first thinking event arrives.
     * Inserted before the assistant placeholder.
     */
    function createThinkingBlock() {
        thinkingWrapper = document.createElement("div");
        thinkingWrapper.className = "thinking-block";

        const avatar = document.createElement("div");
        avatar.className = "message-avatar";
        avatar.textContent = "\uD83E\uDD40";
        thinkingWrapper.appendChild(avatar);

        thinkingEl = document.createElement("details");
        thinkingEl.open = true;
        thinkingWrapper.appendChild(thinkingEl);

        thinkingSummaryEl = document.createElement("summary");
        thinkingSummaryEl.className = "thinking-summary";
        thinkingSummaryEl.textContent = "\uD83D\uDD4E Thinking";
        thinkingEl.appendChild(thinkingSummaryEl);

        thinkingContentEl = document.createElement("div");
        thinkingContentEl.className = "thinking-content";
        thinkingEl.appendChild(thinkingContentEl);

        // Insert before assistant placeholder
        if (assistantEl && assistantEl.parentNode) {
            assistantEl.parentNode.insertBefore(thinkingWrapper, assistantEl);
        } else {
            dom.chatMessages.appendChild(thinkingWrapper);
        }
        scrollToBottom(dom.chatMessages);
    }

    /**
     * Finalize thinking block: close it (collapsed) and render final content.
     */
    function finalizeThinkingBlock() {
        if (!thinkingEl) return;
        // Collapse the thinking block after it's done
        thinkingEl.open = false;
        thinkingContentEl.innerHTML = escapeHtml(thinkingContent.trim());
        scrollToBottom(dom.chatMessages);
    }

    // Build messages array with system prompt
    const messages = [];
    if (state.systemPrompt.trim()) {
        messages.push({ role: "system", content: state.systemPrompt });
    }
    messages.push(...state.chatMessages);

    // Build chat request body
    const body = {
        model: state.selectedModel,
        messages: messages,
        temperature: state.temperature,
        system_prompt: state.systemPrompt,
        toolCallEnabled: state.toolCallEnabled,
    };

    // Clear attachments after sending
    clearAttachments(dom);

    try {
        const response = await apiCallChat(body);

        // Parse SSE stream from Pydantic AI backend
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        let assistantContent = "";
        let streamDone = false;

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
                    if (payload === "") continue;

                    try {
                        const parsed = JSON.parse(payload);

                        // Check for error events
                        if (parsed.__error__) {
                            throw new Error(parsed.__error__);
                        }

                        // Check for usage info (final metadata)
                        if (parsed.__usage__) {
                            metrics.totalTokens = parsed.__usage__.total_tokens || tokenCount;
                            metrics.completionTokens = parsed.__usage__.completion_tokens || 0;
                            metrics.promptTokens = parsed.__usage__.prompt_tokens || 0;
                            updateMetrics(dom, metrics);
                            continue;
                        }

                        // Thinking content update
                        if (parsed.thinking !== undefined) {
                            if (typeof parsed.thinking === "string" && parsed.thinking.length > 0) {
                                thinkingSeen = true;
                                thinkingContent += parsed.thinking;
                                if (!thinkingEl) createThinkingBlock();
                                if (thinkingContentEl) {
                                    thinkingContentEl.textContent = thinkingContent;
                                    scrollToBottom(dom.chatMessages);
                                }
                            }
                            continue;
                        }
                        if (parsed.thinking_full !== undefined) {
                            if (typeof parsed.thinking_full === "string" && parsed.thinking_full.length > 0) {
                                thinkingSeen = true;
                                thinkingContent = parsed.thinking_full;
                                if (!thinkingEl) createThinkingBlock();
                                if (thinkingContentEl) {
                                    thinkingContentEl.textContent = thinkingContent;
                                    scrollToBottom(dom.chatMessages);
                                }
                            }
                            continue;
                        }

                        // Thinking complete marker
                        if (parsed.thinking_done) {
                            thinkingDone = true;

                            // Finalize thinking block if thinking was seen
                            if (thinkingSeen && thinkingContent.trim() && thinkingEl) {
                                finalizeThinkingBlock();
                            }

                            // Update streaming indicator
                            if (dom.streamingIndicatorText) {
                                dom.streamingIndicatorText.textContent = "Streaming response...";
                            }
                            continue;
                        }

                        // Text content delta
                        if (parsed.content !== undefined) {
                            const delta = parsed.content;
                            if (typeof delta === "string" && delta.length > 0) {
                                // If thinking was seen but not finalized, do it now
                                if (!thinkingDone && thinkingSeen && thinkingContent.trim()) {
                                    thinkingDone = true;
                                    if (thinkingEl) finalizeThinkingBlock();
                                    if (dom.streamingIndicatorText) {
                                        dom.streamingIndicatorText.textContent = "Streaming response...";
                                    }
                                }

                                tokenCount++;
                                // TextPart.content from Pydantic AI is the FULL accumulated text,
                                // NOT a delta. So we assign (=) not append (+=).
                                assistantContent = delta;
                                renderContent(contentEl, assistantContent);
                                scrollToBottom(dom.chatMessages);

                                if (!firstTokenTime) {
                                    firstTokenTime = (Date.now() - streamStart) / 1000;
                                }

                                const elapsed = (Date.now() - streamStart) / 1000;
                                metrics.tokensPerSecond = elapsed > 0 ? tokenCount / elapsed : 0;
                                metrics.timeToFirstToken = firstTokenTime;
                                metrics.totalTokens = tokenCount;
                                updateMetrics(dom, metrics);
                            }
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

        // Finalize thinking block if stream ended without thinking_done
        if (!thinkingDone && thinkingSeen && thinkingContent.trim() && thinkingEl) {
            finalizeThinkingBlock();
        }

        // Final metrics
        const totalDuration = (Date.now() - streamStart) / 1000;
        metrics.tokensPerSecond = totalDuration > 0 ? tokenCount / totalDuration : 0;
        metrics.timeToFirstToken = firstTokenTime || 0;
        metrics.totalTokens = tokenCount;
        updateMetrics(dom, metrics);

        // Save assistant message to state
        const assistantMsg = { role: "assistant", content: assistantContent };
        state.chatMessages.push(assistantMsg);
        state.metrics = { ...metrics };

        // Content was already rendered in real-time during the stream,
        // so no need to re-render here

        // Save session to history
        saveCurrentSession();
        renderHistoryList(dom);

    } catch (e) {
        // Hide streaming indicator on error
        if (dom.streamingIndicator) dom.streamingIndicator.style.display = "none";

        // Clean up thinking block and assistant placeholder on error
        if (thinkingWrapper) thinkingWrapper.remove();
        if (assistantEl) assistantEl.remove();

        // Show error message
        const errorEl = document.createElement("div");
        errorEl.className = "message assistant";
        errorEl.innerHTML = `
            <div class="message-avatar">AI</div>
            <div class="message-content">
                <div class="message-role">Assistant</div>
                <div class="error-message">Error: ${escapeHtml(e.message)}</div>
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
