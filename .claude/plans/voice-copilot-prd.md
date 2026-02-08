# Voice Copilot — Problem Definition & Proposed Solution

**Document Version:** 0.1 (Draft)
**Date:** February 8, 2026
**Status:** Ideation & Discovery

---

## 1. Problem Definition

### 1.1 Context

Modern software development increasingly relies on AI coding agents embedded within IDEs (Cursor, VS Code with Copilot, Windsurf) and CLI tools (Claude Code, Aider). These agents operate in a conversational loop: the developer provides a prompt, the agent thinks, writes code, asks clarifying questions, requests permissions, and eventually delivers a result. This loop can span minutes or even longer for complex tasks.

### 1.2 The Core Problem

**Developers have no ambient awareness of what their AI coding agent is doing when they are not actively looking at their IDE.**

This manifests in two specific pain points:

**Pain Point #1 — Silent Blocking**
When an AI agent encounters a decision point — a permission request, a clarifying question, or an option it needs the developer to choose — it stops and waits silently. There is no meaningful notification system that reaches the developer if they have stepped away from the IDE, switched to another application, or are simply not watching the chat panel. The developer may return minutes or even hours later to discover the agent has been idle, waiting for a simple "yes" or "no." This creates significant wasted time and breaks the async workflow that agents are supposed to enable.

**Pain Point #2 — Opaque Agent Activity**
While the agent is actively working (thinking, writing code, modifying files, running commands), the only way to understand what it is doing is to read the text output in the IDE's chat panel. This requires the developer to:

- Be physically present at their screen
- Actively read and parse potentially verbose output
- Context-switch away from whatever else they are doing

There is no passive, ambient channel through which the developer can monitor agent progress without visually reading the IDE. The agent's thinking and reasoning process is entirely text-based and requires active attention to consume.

### 1.3 Why This Matters

The promise of AI coding agents is that developers can delegate tasks and work on other things — review a PR, sketch architecture on a whiteboard, take a break, or work on a parallel task. But the current interaction model undermines this by requiring continuous visual monitoring. The developer is tethered to their screen, constantly checking: "Is it still working? Did it finish? Is it stuck?"

This reduces the productivity gains that agents are supposed to provide, and creates a frustrating experience where the developer is neither fully focused on the agent's task nor fully free to do something else.

### 1.4 Current State of the Market

Existing tools address fragments of this problem but none solve it holistically:

**Voice Narration Tools (Agent → Speech)**

- **AgentVibes** — Adds TTS narration to Claude Code and Claude Desktop sessions. Reads agent output aloud using Piper TTS or ElevenLabs. Provides auditory acknowledgments on task start and completion. However, it reads raw output without intelligent summarization, does not detect blocking/questions, and does not support voice responses back to the agent.
- **agent-tts** — Similar real-time TTS for Claude Code and OpenCode. Supports multiple TTS providers. Same limitations as AgentVibes: no summarization, no question detection, no voice response loop.
- **VoiceMode** — Enables full voice conversations with Claude Code (speak to Claude, hear responses). More of a voice-first interaction mode than a passive monitoring tool.

**Voice Input Tools (Developer → Agent via speech)**

- **Wispr Flow** — A dictation tool that works across all apps and IDEs. Developer speaks, text is typed. Purely an input tool with no agent output narration.
- **Serenade.ai** — Voice-to-code programming tool. Input only.
- **Super Whisper** — Speech-to-text for dictating prompts. Input only.
- **VS Code Speech Extension** — Microsoft's voice input for Copilot chat. Input only.
- **Aider /voice** — Voice command in Aider for recording and transcribing speech into the chat. Input only.

**Notification Tools (Agent → Silent Alert)**

- **ai-agents-notifier** — Desktop notifications for CLI AI agent sessions (completions, permission requests). Works with Claude Code and GitHub Copilot CLI. Silent visual notifications only, no voice.
- **Agentastic.dev** — Mac app with a waiting indicator for tracking which agents need attention. Desktop notifications, no voice.
- **Cursor Sound Notifications** — Cursor v0.48+ has basic sound notifications and visual indicators, but these don't work when Cursor is minimized or the developer is in another app.

**The Gap:** No existing tool combines intelligent summarization of agent output, real-time voice narration, blocking/question detection with audio alerts, and voice-based response back to the agent — all in a single product that works across multiple IDEs.

---

## 2. Proposed Solution

### 2.1 Overview

An extension (eventually a standalone app/service) that acts as a real-time audio bridge between the developer and their AI coding agent. It listens to the agent's output stream, intelligently summarizes it, narrates progress aloud via text-to-speech, detects when the agent is blocked or awaiting input, alerts the developer audibly, and accepts voice responses to unblock the agent.

Working title: **"Voice Copilot"** (placeholder).

### 2.2 The Pipeline

The system operates as a five-stage pipeline:

**Stage 1 — Intercept**
Capture the agent's output stream from the IDE or terminal in real time. The interception method varies by platform:

- For VS Code / Cursor / Windsurf: via the extension API, hooking into the chat panel's output stream
- For Claude Code (terminal-based): intercepting stdout — the text output that Claude Code prints to the terminal

**Stage 2 — Filter & Summarize**
Process the intercepted text through a summarization layer before sending it to TTS. This is a critical differentiator from existing tools like AgentVibes which read raw output verbatim. The summarization layer:

- **Skips the "thinking" portion** — AI agents often produce verbose internal reasoning. This is too noisy to narrate. The system filters this out.
- **Narrates results and actions** — What the agent actually did: files created, code modified, commands run, tests passed/failed. Example: "Refactored the auth module. Created three new files. Running tests now."
- **Detects questions and permission requests** — Identifies when the agent has stopped and is waiting for developer input. This is the highest-priority content.
- **Extracts available options** — When the agent presents choices (e.g., "Should I use approach A or approach B?"), the system extracts and narrates the options clearly.

The narration should be continuous for agent results (not thinking), providing an ambient audio stream of what the agent is accomplishing.

**Stage 3 — Text-to-Speech (TTS)**
Convert the summarized text to natural-sounding speech and play it aloud. The proposed TTS stack:

- **ElevenLabs** for high-quality, natural-sounding voice output
- **LiveKit** as the real-time audio infrastructure layer
- Other providers can be supported as alternatives

**Stage 4 — Question Detection & Alert**
When the agent is blocked and awaiting input, the system triggers a distinct audio alert (differentiated from normal narration) and reads out:

- The question or permission request the agent is asking
- The available options, if any

This ensures the developer knows immediately — even if they are in another room — that the agent has stopped and needs their input. This eliminates the "silent blocking" problem entirely.

**Stage 5 — Speech-to-Text (STT) & Voice Response**
Allow the developer to respond verbally to unblock the agent:

- The system listens for the developer's spoken response
- Converts speech to text via STT
- Maps the response to the appropriate option or input
- Feeds the response back into the IDE/agent to continue execution

This closes the loop: the developer can monitor and interact with their agent entirely through audio, without needing to return to their screen.

### 2.3 Key Design Decisions

| Decision | Choice | Rationale |
|---|---|---|
| What to narrate | Agent results and actions, not thinking/reasoning | Thinking is too verbose and noisy. Developers need to know what happened, not the internal chain of thought. |
| Narration style | Continuous for results | Provides ambient awareness without requiring active attention. Developer can passively listen while doing other work. |
| Initial platform | VS Code / Cursor extension | Largest developer audience. Extension API provides clean integration. |
| Long-term platform | Standalone app / service | Enables cross-IDE and cross-tool support without being locked to one ecosystem. |
| TTS provider | ElevenLabs via LiveKit | High-quality, natural-sounding voices. LiveKit provides real-time audio infrastructure. |
| Voice response | STT with option selection | Developer hears the options, speaks their choice, system feeds it back to the agent. Enables fully hands-free interaction. |
| Priority levels / volume control | Deferred | Not a priority for the initial version. Can be layered on later. |

### 2.4 User Experience (Narrative)

**Scenario: Developer gives a prompt and walks away**

1. Developer types a prompt in Cursor: "Refactor the authentication module to use JWT tokens and add unit tests."
2. Developer walks to the kitchen to make coffee.
3. Voice Copilot narrates (ambient, normal volume): *"Starting work on the auth module... Modifying auth-service.ts... Created jwt-helper.ts... Updating three test files..."*
4. The agent encounters a decision: "Should I use RS256 or HS256 for the JWT signing algorithm?"
5. Voice Copilot plays a distinct alert tone, then narrates (clear, slightly louder): *"The agent has a question and is waiting for your answer. It's asking: Should it use RS256 or HS256 for JWT signing? Option one: RS256. Option two: HS256."*
6. Developer, from the kitchen, says: "Option one."
7. Voice Copilot converts speech to text, feeds "RS256" back to the agent.
8. Agent continues. Voice Copilot resumes ambient narration: *"Proceeding with RS256... Generating key pair... Tests passing. Task complete."*

The developer never returned to their screen, yet the task was completed without delay.

---

## 3. Open Questions & Next Steps

The following areas have not yet been discussed or decided and require further exploration:

### 3.1 Technical Architecture
- How exactly will the extension intercept the chat output from VS Code / Cursor? Which APIs are available?
- What is the latency budget for the intercept → summarize → TTS pipeline?
- How will the summarization layer work? A local LLM? An API call to a cloud model? Rule-based heuristics?
- How will the system distinguish between "thinking" text and "result" text across different agents (Copilot, Claude, Cursor's built-in agent)?

### 3.2 Product Scope
- What is the MVP? Which stages of the pipeline are essential for a first version vs. which can come later?
- Should voice response (Stage 5) be in the MVP or is narration + alert (Stages 1–4) sufficient to validate the idea?
- How will the product be distributed? VS Code Marketplace? Standalone installer?

### 3.3 Platform Support
- What is the priority order for IDE/tool support beyond VS Code/Cursor?
- How will the system handle different agent formats? (Copilot's output structure vs. Claude Code's stdout vs. Cursor's agent mode)

### 3.4 Monetization & Business Model
- Free / freemium / paid?
- Where does the cost lie? (TTS API calls to ElevenLabs, potential summarization LLM costs)

### 3.5 Competitive Positioning
- AgentVibes is the closest existing tool. How does Voice Copilot differentiate clearly in messaging?
- Is there a risk of IDE vendors (Cursor, GitHub Copilot) building this natively?

---

## 4. Competitive Landscape Summary

| Tool | Narrates Agent Output | Smart Summarization | Question/Block Detection + Alert | Voice Response to Agent | Multi-IDE Support |
|---|---|---|---|---|---|
| **AgentVibes** | ✅ | ❌ (raw output) | ❌ | ❌ | ❌ (Claude only) |
| **agent-tts** | ✅ | ❌ (raw output) | ❌ | ❌ | ❌ (Claude/OpenCode) |
| **VoiceMode** | ✅ (full conversation) | ❌ | ❌ | ✅ (full conversation) | ❌ (Claude only) |
| **Wispr Flow** | ❌ | ❌ | ❌ | ❌ (input only) | ✅ |
| **ai-agents-notifier** | ❌ | ❌ | ✅ (silent notification) | ❌ | ❌ (CLI only) |
| **Agentastic.dev** | ❌ | ❌ | ✅ (silent notification) | ❌ | ❌ (Mac only) |
| **Cursor Sound Notifications** | ❌ | ❌ | Partial (sound only) | ❌ | ❌ (Cursor only) |
| **Voice Copilot (Proposed)** | ✅ | ✅ | ✅ | ✅ | ✅ (goal) |

---

*This document captures the problem definition and proposed solution as discussed. No technical architecture, implementation plan, or MVP scope has been finalized. These are the recommended next steps for further discussion.*


Stage 1 (Intercept) → captures events from Claude Code
Stage 2 (Filter & Summarize) → local LLM summarizes events into concise narration text (e.g., "Edited auth.ts. Running tests now.")
Stage 3 (TTS) → takes that summarized text and converts it to speech via ElevenLabs/LiveKit

Specifically, LiveKit serves as the real-time audio infrastructure layer — it handles audio streaming, playback, and potentially the bidirectional voice channel needed for Stage 5 (voice response back to the agent). ElevenLabs is the actual voice synthesis engine that generates natural-sounding speech.

Stage 3 subscribes to the event bus we're building in Stage 1, receives summarized text from Stage 2, and pipes it to TTS for playback. We're not building Stage 3 right now — just making sure the event bus output is clean enough to feed into it later.