/**
 * Chat functionality: send messages, handle streaming responses, render messages,
 * track metrics, file attachments.
 *
 * Pydantic AI runs the tool loop on the backend and streams normalized
 * content, thinking, and tool lifecycle events to this module.
 */

import { state, saveSettings, saveCurrentSession, saveSessionHistory, abortActiveRequest } from "./state.js";
import { apiCallChat, apiCall } from "./api.js";
import { showToast, scrollToBottom, autoResizeInput, updateMetrics, escapeHtml } from "./ui.js";
import { renderHistoryList } from "./history.js";

// Initialize mermaid
try {
    mermaid.initialize({
        startOnLoad: false,
        theme: "dark",
        // "strict" disables click handlers and other interactive features in
        // rendered diagrams, limiting the attack surface of model-generated
        // diagram definitions.
        securityLevel: "strict",
    });
} catch (e) {
    // mermaid may not be loaded yet
}

/**
 * SVG markup for the stop (filled square) icon used in stop mode.
 */
const STOP_ICON_SVG =
    '<svg viewBox="0 0 24 24" fill="currentColor" stroke="none" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><rect x="6" y="6" width="12" height="12" rx="2"></rect></svg>';

/**
 * Default send icon, used if the original button markup was not captured.
 */
const SEND_ICON_SVG =
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="22" y1="2" x2="11" y2="13"></line><polygon points="22 2 15 22 9 22 22 2"></polygon></svg>';

/**
 * Captured original innerHTML of the send button, used to restore the send
 * icon after stop mode. Module-level so cancelRequest() can also restore it.
 */
let _originalSendBtnHTML = null;

/**
 * Swap the send button into "stop mode": add the stop-mode class, swap the SVG
 * icon to a filled square, and keep the button enabled so the user can click
 * it to cancel. Prefers the dedicated #sendBtnIcon element if present
 * (re-queried to avoid stale references), otherwise swaps the button's SVG.
 * @param {Object} dom - DOM element references.
 */
function enterStopMode(dom) {
    dom.sendBtn.classList.add("stop-mode");
    dom.sendBtn.disabled = false; // keep enabled so the user can click to cancel
    const iconEl = document.getElementById("sendBtnIcon") || dom.sendBtn.querySelector("svg");
    if (iconEl) {
        iconEl.outerHTML = STOP_ICON_SVG;
    } else {
        dom.sendBtn.innerHTML = STOP_ICON_SVG;
    }
}

/**
 * Restore the send button to "send mode": remove the stop-mode class, restore
 * the original SVG icon, and (unless skipDisabled) re-enable based on model
 * selection.
 *
 * skipDisabled is passed as true by sendMessage's finally block when the
 * exit was a navigation abort: in that case the navigation handler owns the
 * disabled state, and deriving it from !state.selectedModel here would
 * clobber it (race where Continue targets an unloaded model B: this would
 * re-enable the button because B is truthy, undoing Continue's
 * hasLoadedModel-based disable).
 * @param {Object} dom - DOM element references.
 * @param {string|null} originalHTML - The original button innerHTML to restore.
 * @param {boolean} [skipDisabled=false] - When true, leave the button's
 *   disabled state untouched so a navigation handler can own it.
 */
function restoreSendMode(dom, originalHTML, skipDisabled = false) {
    dom.sendBtn.classList.remove("stop-mode");
    dom.sendBtn.innerHTML = originalHTML && originalHTML.length > 0 ? originalHTML : SEND_ICON_SVG;
    if (!skipDisabled) {
        dom.sendBtn.disabled = !state.selectedModel;
    }
}

/**
 * Start a new chat session (clear history).
 * @param {Object} dom - DOM element references.
 */
export function newChat(dom) {
    // Cancel any in-flight request and reset the streaming UI before
    // clearing state, so the backend stream is torn down server-side and
    // the stop button / "Generating..." indicator don't linger on the
    // cleared view while the aborted fetch settles. Matches
    // continueSession / disconnect, which also use cancelAndResetUI.
    // cancelAndResetUI leaves state.streaming true (it only aborts the
    // fetch), so state.streaming stays true until the aborted
    // sendMessage's finally runs — keeping the entry guard blocking
    // re-entrant sends during the settle window.
    cancelAndResetUI(dom);

    // Save current session before clearing
    if (state.chatMessages.length > 0) {
        saveCurrentSession();
        renderHistoryList(dom);
    }

    state.chatMessages = [];
    state.currentSessionId = null;
    // Note: state.streaming is intentionally NOT reset here. See the
    // cancelAndResetUI note above — resetting it would re-open a window
    // for re-entrant sendMessage calls while the aborted request settles.
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
 * @param {AbortSignal} [signal] - Optional AbortSignal to cancel the in-flight
 *   upload so the stop button can abort multi-file uploads, not just the
 *   subsequent /api/chat fetch.
 * @returns {Promise<Object>} Upload result with base64 content
 */
async function uploadFile(file, signal = undefined) {
    const formData = new FormData();
    formData.append("file", file);

    const response = await fetch("/api/upload", {
        method: "POST",
        body: formData,
        signal,
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
            const dataUri = `data:${att.mimeType};base64,${att.base64}`;
            if (att.isImage) {
                content.push({ type: "image_url", image_url: { url: dataUri } });
            } else if (att.mimeType.startsWith("audio/")) {
                content.push({ type: "input_audio", file_data: dataUri });
            } else {
                content.push({ type: "input_file", file_data: dataUri });
            }
        }
    }

    return content;
}

/**
 * Send a message and receive a streaming response via Pydantic AI.
 * The backend handles the tool loop and streams normalized lifecycle events.
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
    // Swap the send button into "stop mode" (still enabled) so the user can
    // cancel the in-flight request. Track the controller so cancelRequest()
    // and navigation paths (newChat/continueSession/disconnect) can abort
    // the underlying fetch.
    const controller = new AbortController();
    state.abortController = controller;
    _originalSendBtnHTML = dom.sendBtn.innerHTML;
    enterStopMode(dom);
    if (dom.attachBtn) dom.attachBtn.disabled = true;
    dom.chatInput.value = "";
    autoResizeInput(dom.chatInput);

    // W1: Capture the chatMessages array reference as a per-send sentinel
    // before the fetch. The abort catch block runs asynchronously when the
    // aborted fetch settles, which can be AFTER a navigation handler
    // (newChat / continueSession) has replaced state.chatMessages with a
    // different session's messages (newChat assigns a fresh [] array;
    // continueSession assigns [...session.messages]). Guarding
    // preservePartial with this array-identity check prevents the orphaned
    // partial assistant message from being pushed into the now-different
    // session and corrupting it. Unlike currentSessionId — which collapses
    // to null for consecutive new chats, making null === null falsely pass —
    // the array reference is unique per session load, so this identity check
    // is robust regardless of session IDs.
    const sendMessages = state.chatMessages;

    // Tracking variables and helper closures are declared in the function
    // scope (before the try) so the catch block below can access them. The
    // try wraps the upload loop too (so an upload abort can `return` early
    // and still hit the finally for cleanup), which means these can't live
    // inside it. streamStart / tokenCount / firstTokenTime stay local to the
    // try since the catch never reads them.
    const currentAttachments = [...state.attachments];
    const metrics = { tokensPerSecond: 0, timeToFirstToken: null, totalTokens: 0 };
    let thinkingContent = "";
    let thinkingDone = false;
    let thinkingWrapper = null; // outer <div class="thinking-block">
    let thinkingEl = null;      // inner <details>
    let thinkingContentEl = null;
    let thinkingSummaryEl = null;
    let thinkingSeen = false;   // tracks if any thinking events arrived
    const thinkingWrappers = [];
    const toolCallMap = new Map(); // tool_call_id -> DOM element
    let assistantEl = null;
    let contentEl = null;
    let assistantContent = "";

    // Throttled, race-free streaming render (Issue 18): re-running
    // renderContent() on the ENTIRE message for every streamed token is
    // O(n^2) markdown re-parsing. Bounds renders to at most one per
    // CONTENT_RENDER_INTERVAL_MS and serializes them on a promise chain so
    // a slow async mermaid.render can never interleave with a newer
    // innerHTML write. The final render always emits the complete content,
    // so a finished message renders identically to the old per-delta path.
    const CONTENT_RENDER_INTERVAL_MS = 100;
    let renderChain = Promise.resolve();
    let renderPending = false;
    let lastRenderAt = 0;

    function scheduleContentRender() {
        const now = performance.now();
        if (now - lastRenderAt >= CONTENT_RENDER_INTERVAL_MS) {
            lastRenderAt = now;
            renderPending = false;
            renderChain = renderChain
                .then(() => renderContent(contentEl, assistantContent))
                .catch(() => {});
        } else {
            renderPending = true;   // a later render will pick up full content
        }
    }

    async function awaitFinalContentRender() {
        renderChain = renderChain
            .then(() => renderPending ? renderContent(contentEl, assistantContent) : undefined)
            .catch(() => {});
        renderPending = false;
        await renderChain;
    }

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
        thinkingWrappers.push(thinkingWrapper);
        scrollToBottom(dom.chatMessages);
    }

    /**
     * Start a new thinking block after a completed model turn. Tool loops can
     * produce reasoning before and after a tool call; keeping separate blocks
     * preserves the visible order of thinking → tool → thinking → answer.
     */
    function beginThinkingBlock() {
        if (thinkingDone) {
            thinkingContent = "";
            thinkingWrapper = null;
            thinkingEl = null;
            thinkingContentEl = null;
            thinkingSummaryEl = null;
            thinkingDone = false;
        }
        thinkingSeen = true;
        if (!thinkingEl) createThinkingBlock();
    }

    /**
     * Remove every thinking block created for this response.
     */
    function removeThinkingBlocks() {
        for (const wrapper of thinkingWrappers) wrapper.remove();
        thinkingWrappers.length = 0;
        thinkingWrapper = null;
        thinkingEl = null;
        thinkingContentEl = null;
        thinkingSummaryEl = null;
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

    /**
     * Create a tool call element and insert before the assistant placeholder.
     */
    function createToolCallElement(toolCallId, name, args) {
        // A provider may expose a call more than once while its streamed
        // arguments are assembled. Keep one visible card per ID and update
        // it in place rather than leaving duplicate "executing" cards.
        const existing = toolCallMap.get(toolCallId);
        if (existing) {
            const argsText = formatToolArguments(args);
            existing.argsEl.textContent = argsText;
            return existing.el;
        }

        const el = document.createElement("div");
        el.className = "tool-call";

        const avatar = document.createElement("div");
        avatar.className = "message-avatar";
        avatar.textContent = "🔧";
        el.appendChild(avatar);

        const content = document.createElement("div");
        content.className = "tool-call-content";

        const header = document.createElement("div");
        header.className = "tool-call-header";

        const nameEl = document.createElement("span");
        nameEl.className = "tool-call-name";
        nameEl.textContent = name.replace(/_/g, " ");
        header.appendChild(nameEl);

        const statusEl = document.createElement("span");
        statusEl.className = "tool-call-status executing";
        statusEl.textContent = "executing";
        header.appendChild(statusEl);
        content.appendChild(header);

        const argsEl = document.createElement("div");
        argsEl.className = "tool-call-args";
        argsEl.textContent = formatToolArguments(args);
        content.appendChild(argsEl);

        // Result placeholder (hidden until done)
        const resultEl = document.createElement("div");
        resultEl.className = "tool-call-result";
        resultEl.style.display = "none";
        content.appendChild(resultEl);

        el.appendChild(content);

        // Insert before assistant placeholder
        if (assistantEl && assistantEl.parentNode) {
            assistantEl.parentNode.insertBefore(el, assistantEl);
        } else {
            dom.chatMessages.appendChild(el);
        }

        // Store reference for later update (keyed by tool_call_id)
        toolCallMap.set(toolCallId, { el, statusEl, argsEl, resultEl });
        scrollToBottom(dom.chatMessages);
        return el;
    }

    /**
     * Finalize a tool call: update status and show result.
     * @param {string} toolCallId - The tool call ID.
     * @param {string} status - "done" or "error".
     * @param {string|null} result - Result text (may be null for errors).
     */
    function finalizeToolCall(toolCallId, status, result) {
        const entry = toolCallMap.get(toolCallId);
        if (!entry) return;

        entry.statusEl.className = `tool-call-status ${status}`;
        entry.statusEl.textContent = status;

        if (result !== null && result !== undefined && result !== "") {
            entry.resultEl.style.display = "block";
            entry.resultEl.textContent = typeof result === "string"
                ? result
                : JSON.stringify(result, null, 2);
        }

        toolCallMap.delete(toolCallId);
        scrollToBottom(dom.chatMessages);
    }

    /**
     * Normalize tool arguments for a readable, stable card representation.
     * Providers may send JSON text, an object, or null.
     */
    function formatToolArguments(args) {
        if (args === null || args === undefined || args === "") return "{}";
        if (typeof args !== "string") return JSON.stringify(args, null, 2);
        try {
            return JSON.stringify(JSON.parse(args), null, 2);
        } catch {
            return args;
        }
    }

    try {
        // Upload attachments to get base64 data. The controller.signal is
        // forwarded to each upload so the stop button (cancelRequest) and
        // navigation handlers (abortActiveRequest) can abort in-flight
        // uploads, not just the subsequent /api/chat fetch. On abort we break
        // out of the loop so remaining files are not uploaded.
        for (const att of currentAttachments) {
            try {
                const result = await uploadFile(att.file, controller.signal);
                att.uploaded = true;
                att.base64 = result.base64;
                att.mimeType = result.mimeType;
                att.isImage = result.isImage;
            } catch (e) {
                if (e && e.name === "AbortError") {
                    break;
                }
                showToast(`Failed to upload ${att.name}: ${e.message}`, "error");
            }
        }

        // W2: If the upload was aborted (stop button or navigation), exit
        // early BEFORE pushing the user message, creating the assistant
        // placeholder, showing the streaming indicator, or calling
        // apiCallChat. Otherwise the already-aborted signal would make
        // apiCallChat reject immediately, leaving a dangling user message
        // and a UI flash of the placeholder / indicator. The finally block
        // below still runs for full cleanup (reset streaming, restore the
        // send button, re-enable the input).
        if (controller.signal.aborted) return;

        // Build multimodal content
        const userContent = buildMultimodalContent(text, currentAttachments);

        // Add user message
        const userMsg = { role: "user", content: userContent };
        state.chatMessages.push(userMsg);

        // Render user message with attachment indicators
        appendMessage(dom, userMsg, "user", currentAttachments);

        // Reset per-stream metrics. streamStart / tokenCount / firstTokenTime
        // are local to the stream and only used within this try.
        const streamStart = Date.now();
        let tokenCount = 0;
        let firstTokenTime = null;

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

        // System prompt is sent separately via body.system_prompt and injected
        // server-side; do not also prepend it as a system message.
        const messages = [...state.chatMessages];

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

        const response = await apiCallChat(body, controller.signal);

        // Parse SSE stream from Pydantic AI backend
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
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

                        // Server-side cancellation is a terminal stream state.
                        // Do not treat it as a successful completion or save a
                        // partial assistant response in the normal path.
                        if (parsed.__cancelled__) {
                            streamDone = true;
                            const cancellationError = new DOMException("The chat request was cancelled", "AbortError");
                            throw cancellationError;
                        }

                        // Check for error events. Tool-result events are sent
                        // before this marker by the backend, so visible cards
                        // retain their error state.
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
                                beginThinkingBlock();
                                thinkingContent += parsed.thinking;
                                if (thinkingContentEl) {
                                    thinkingContentEl.textContent = thinkingContent;
                                    scrollToBottom(dom.chatMessages);
                                }
                            }
                            continue;
                        }
                        if (parsed.thinking_full !== undefined) {
                            if (typeof parsed.thinking_full === "string" && parsed.thinking_full.length > 0) {
                                beginThinkingBlock();
                                thinkingContent = parsed.thinking_full;
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

                        // Tool call event (executing)
                        if (parsed.tool_call !== undefined) {
                            const tc = parsed.tool_call;
                            if (tc.name && tc.status === "executing") {
                                if (dom.streamingIndicatorText) {
                                    dom.streamingIndicatorText.textContent = `Running ${tc.name.replace(/_/g, " ")}...`;
                                }
                                createToolCallElement(tc.tool_call_id, tc.name, tc.args ?? "{}");
                            }
                            continue;
                        }

                        // Tool result event (done or error)
                        if (parsed.tool_result !== undefined) {
                            const tr = parsed.tool_result;
                            if (tr.tool_call_id && (tr.status === "done" || tr.status === "error")) {
                                finalizeToolCall(tr.tool_call_id, tr.status, tr.result || null);
                                if (dom.streamingIndicatorText) {
                                    dom.streamingIndicatorText.textContent = "Streaming response...";
                                }
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
                                // Backend emits incremental deltas for TextPart, so we accumulate.
                                assistantContent += delta;
                                // Throttled: at most one full re-render per interval; the
                                // final render after the stream ends is always complete.
                                scheduleContentRender();
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

        // A normal end without a matching tool result is not a successful
        // execution. Keep the card visible and make the incomplete state
        // explicit instead of silently marking it done.
        for (const [toolCallId] of toolCallMap) {
            finalizeToolCall(toolCallId, "error", "Tool execution did not complete");
        }
        toolCallMap.clear();

        // Finalize thinking block if stream ended without thinking_done
        if (!thinkingDone && thinkingSeen && thinkingContent.trim() && thinkingEl) {
            finalizeThinkingBlock();
        }

        // Final metrics
        const totalDuration = (Date.now() - streamStart) / 1000;
        // Use completion_tokens from backend usage for accurate TPS.
        // tokenCount is approximate (1 per SSE event, which may contain multiple tokens).
        const finalTokenCount = metrics.completionTokens || tokenCount;
        metrics.tokensPerSecond = totalDuration > 0 ? finalTokenCount / totalDuration : 0;
        metrics.timeToFirstToken = firstTokenTime || 0;
        metrics.totalTokens = finalTokenCount;
        updateMetrics(dom, metrics);

        // Save assistant message to state
        const assistantMsg = { role: "assistant", content: assistantContent };
        state.chatMessages.push(assistantMsg);
        state.metrics = { ...metrics };

        // Flush the throttled render chain so the final document is always
        // fully rendered, even if the last delta was throttled away (this is
        // a no-op when no render was deferred).
        await awaitFinalContentRender();

        // Save session to history
        saveCurrentSession();
        renderHistoryList(dom);

    } catch (e) {
        const isAbort = e && e.name === "AbortError";

        // Hide streaming indicator on any error/abort
        if (dom.streamingIndicator) dom.streamingIndicator.style.display = "none";

        if (isAbort) {
            // Finalize any pending tool calls that weren't resolved
            for (const [toolCallId] of toolCallMap) {
                finalizeToolCall(toolCallId, "error", "Tool execution cancelled");
            }
            toolCallMap.clear();

            // Finalize thinking block if stream ended without thinking_done
            if (!thinkingDone && thinkingSeen && thinkingContent.trim() && thinkingEl) {
                finalizeThinkingBlock();
            }

            // Preserve partial content only for stop-button aborts
            // (cancelRequest), which set state.abortReason = "stop".
            // Navigation aborts (newChat/continueSession/disconnect via
            // abortActiveRequest) set state.abortReason = "navigate", so the
            // partial content is discarded along with the cleared/replaced
            // chat.
            //
            // Additionally guard with the per-send chatMessages sentinel
            // (sendMessages, captured before the fetch): this catch runs
            // asynchronously when the aborted fetch settles, which can be
            // after the user clicked Stop and then immediately clicked
            // NewChat/Continue (replacing state.chatMessages with a different
            // array). The identity check state.chatMessages === sendMessages
            // ensures the array hasn't been replaced by newChat (which assigns
            // a fresh []) or continueSession (which assigns [...session.messages])
            // since this send started. Without this guard the orphaned partial
            // assistant message would be pushed into the now-different session.
            // Unlike a currentSessionId sentinel — which collapses to null for
            // consecutive new chats (null === null falsely passes) — the array
            // reference is unique per session load, so this check is robust.
            const preservePartial =
                state.abortReason === "stop" && state.chatMessages === sendMessages;

            if (preservePartial && assistantContent.length > 0) {
                state.chatMessages.push({ role: "assistant", content: assistantContent });
                state.metrics = { ...metrics };
                saveCurrentSession();
                renderHistoryList(dom);
            } else {
                // No partial content to keep, or the chat was replaced: remove
                // the now-orphaned placeholder elements.
                removeThinkingBlocks();
                if (assistantEl) assistantEl.remove();
            }

            showToast("Cancelled", "info");
        } else {
            // Clean up thinking block, assistant placeholder, and tool calls on error
            removeThinkingBlocks();
            if (assistantEl) assistantEl.remove();
            for (const [, entry] of toolCallMap) {
                entry.el.remove();
            }
            toolCallMap.clear();

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
    } finally {
        // Capture whether this exit was triggered by a navigation abort
        // (newChat/continueSession/disconnect/deleteSession) before clearing
        // the reason, so we can avoid refocusing the input and re-deriving
        // the disabled state below.
        const wasNavigateAbort = state.abortReason === "navigate";
        // Cleanup on ALL exit paths (success, error, abort)
        state.abortController = null;
        state.abortReason = null;
        // When this exit was triggered by a navigation abort, the navigation
        // handler has already set the appropriate disabled state (and may
        // set it again after this finally runs, e.g. newChat runs
        // synchronously after cancelAndResetUI). Pass wasNavigateAbort so
        // restoreSendMode skips deriving sendBtn.disabled from
        // !state.selectedModel — otherwise this finally would clobber the
        // navigation handler's settings in the race where Continue targets a
        // session whose model B is NOT loaded: Continue sets disabled=true
        // (hasLoadedModel=false), then this finally would wrongly re-enable
        // the button because B is truthy.
        restoreSendMode(dom, _originalSendBtnHTML, wasNavigateAbort);
        _originalSendBtnHTML = null;
        // state.streaming stays true until this finally so the entry guard
        // blocks re-entrant sendMessage calls while a cancelled request
        // settles. cancelRequest / cancelAndResetUI only abort the fetch;
        // this finally owns the streaming reset.
        state.streaming = false;
        // For non-navigate exits (normal completion, error, stop-button
        // abort) derive the input/attach disabled state from the current
        // model selection and refocus the input. For navigation aborts the
        // navigation handler owns the disabled state (and focus), so we skip
        // both to avoid the race where Continue targets an unloaded model B:
        // Continue sets disabled=true via hasLoadedModel, then this finally
        // would re-enable controls because B is truthy.
        if (!wasNavigateAbort) {
            dom.chatInput.disabled = !state.selectedModel;
            if (dom.attachBtn) dom.attachBtn.disabled = !state.selectedModel;
            dom.chatInput.focus();
        }
    }
}

/**
 * Cancel the active in-flight chat request (stop button handler).
 *
 * Aborts the fetch via the stored AbortController. The actual content
 * finalization (preserving partial content, hiding the indicator, showing
 * the "Cancelled" toast) is handled by sendMessage()'s abort catch block
 * when the abort propagates. This function defensively hides the indicator
 * and restores the send button so the UI reflects the cancellation
 * immediately, even before the abort settles.
 *
 * Does NOT clear state.streaming; it stays true until the aborted
 * request's sendMessage finally runs the full cleanup (streaming = false).
 * Clearing streaming here would let a new sendMessage start while the old
 * one settles, and the old finally would clobber the new send's controller
 * / stop-mode.
 *
 * Guards on `state.abortController` before touching anything (mirroring
 * abortActiveRequest). Without this guard, a cancelRequest call arriving
 * during the abort-settle window after a navigation abort (when
 * abortController is already null) would clobber abortReason = "navigate"
 * with "stop", causing sendMessage's catch to treat it as a stop-button
 * abort and preserve partial content into an already-cleared chat.
 *
 * Sets `state.abortReason = "stop"` before aborting so sendMessage's
 * catch/finally know to preserve partial content and refocus the input.
 * Nulls `state.abortController` after aborting (matching abortActiveRequest)
 * so subsequent cancelRequest / abortActiveRequest calls are clean no-ops.
 * @param {Object} dom - DOM element references.
 */
export function cancelRequest(dom) {
    // Guard on the controller (mirroring abortActiveRequest): if there is
    // no in-flight request, this is a no-op. Without this guard a
    // cancelRequest arriving during the abort-settle window after a
    // navigation abort would clobber abortReason = "navigate" with "stop"
    // and sendMessage's catch would preserve partial content into an
    // already-cleared chat.
    if (!state.abortController) return;
    state.abortReason = "stop";
    state.abortController.abort();
    state.abortController = null;
    if (dom.streamingIndicator) dom.streamingIndicator.style.display = "none";
    restoreSendMode(dom, _originalSendBtnHTML);
    if (dom.chatInput) dom.chatInput.disabled = false;
}

/**
 * Cancel the active in-flight chat request and immediately reset the
 * streaming UI to a non-streaming state.
 *
 * Used by navigation handlers (newChat, continueSession, disconnect,
 * deleteSession) that need to tear down an active stream synchronously
 * rather than waiting for sendMessage's deferred finally block. Without
 * this, there is a visible window where the red pulsing stop button and
 * "Generating..." indicator remain on the restored/disconnected view
 * until the aborted fetch settles.
 *
 * - If no stream is active and the button is not in stop mode, returns
 *   immediately WITHOUT aborting, so a no-op call (e.g. clicking Continue
 *   / Disconnect while idle) does not leave a stale abortReason =
 *   "navigate" behind (which would make a later normal completion skip
 *   refocusing).
 * - Otherwise aborts the fetch via abortActiveRequest() (which sets
 *   state.abortReason = "navigate" only when a controller exists, so
 *   sendMessage's catch/finally discard partial content and skip
 *   refocusing).
 * - Does NOT clear state.streaming; it stays true until the aborted
 *   request's sendMessage finally runs the full cleanup (streaming =
 *   false). Clearing streaming here would let a new sendMessage start
 *   while the old one settles, and the old finally would clobber the new
 *   send's controller / stop-mode.
 * - Hides the streaming indicator.
 * - Restores the send button to send mode (only when currently in stop
 *   mode, to avoid disturbing the #sendBtnIcon element when no stream is
 *   active).
 * - Sets the chat input disabled state based on the current model
 *   selection (disabled when no model is selected).
 *
 * Does NOT null _originalSendBtnHTML; sendMessage's finally owns that so it
 * can re-run restoreSendMode idempotently.
 * @param {Object} dom - DOM element references.
 */
export function cancelAndResetUI(dom) {
    // If no stream is active and the button is not in stop mode, there is
    // nothing to reset. Check this BEFORE aborting so a no-op call (e.g.
    // clicking Continue / Disconnect while idle) does not set a stale
    // abortReason = "navigate" via abortActiveRequest. Also avoids calling
    // restoreSendMode with a null original (which would replace the
    // #sendBtnIcon element with the fallback SVG, losing the id needed by
    // enterStopMode).
    if (!state.streaming && !(dom.sendBtn && dom.sendBtn.classList.contains("stop-mode"))) {
        return;
    }
    // Abort the in-flight fetch (sets abortReason = "navigate" only when a
    // controller exists). sendMessage's catch/finally will discard partial
    // content and skip refocusing accordingly. state.streaming is
    // intentionally left true so the entry guard blocks re-entrant sends
    // until the aborted request's finally runs the full cleanup.
    abortActiveRequest();
    if (dom.streamingIndicator) dom.streamingIndicator.style.display = "none";
    restoreSendMode(dom, _originalSendBtnHTML);
    if (dom.chatInput) dom.chatInput.disabled = !state.selectedModel;
}

/**
 * Set an element's innerHTML, sanitizing it with DOMPurify when available.
 * Model output is untrusted: sanitizing strips <script> tags, event-handler
 * attributes, and other XSS vectors before the HTML touches the DOM.
 * @param {HTMLElement} el - The element to update
 * @param {string} html - HTML markup to assign
 */
function _setHtml(el, html) {
    el.innerHTML = (typeof DOMPurify !== "undefined") ? DOMPurify.sanitize(html) : html;
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

    // Mermaid is a pinned CDN script; if it failed to load (offline), fall
    // back to plain sanitized markdown so content still renders.
    if (hasMermaid && typeof mermaid !== "undefined") {
        _setHtml(el, marked.parse(content));

        const preElements = el.querySelectorAll("pre");
        let mermaidId = 0;

        for (const pre of preElements) {
            const code = pre.querySelector("code");
            // marked renders ```mermaid fences as class="language-mermaid";
            // accept a bare "mermaid" class too for defensive coverage.
            if (code && (code.classList.contains("mermaid") || code.classList.contains("language-mermaid"))) {
                const graphDef = code.textContent;
                const svgId = `mermaid-${mermaidId++}`;

                try {
                    const { svg } = await mermaid.render(svgId, graphDef);
                    pre.classList.add("mermaid-pre");
                    const svgDiv = document.createElement("div");
                    svgDiv.className = "mermaid";
                    _setHtml(svgDiv, svg);
                    pre.parentNode.insertBefore(svgDiv, pre.nextSibling);
                } catch (e) {
                    pre.classList.remove("mermaid-pre");
                }
            }
        }
    } else {
        _setHtml(el, marked.parse(content));
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
