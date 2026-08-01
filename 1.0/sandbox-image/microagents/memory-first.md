---
name: memory-first
type: knowledge
version: 1.0.0
agent: CodeActAgent
triggers:
- search
- internet
- web
- docs
- documentation
- lm studio
- openhands
- memory
- mempalace
- model
- qwen
---

# Memory-First Research Policy

Before doing internet research or repeating a configuration investigation, check
the user's durable memory first.

Use these tools in order:

1. `memory-search "<query>"` to check MemPalace for previous answers.
2. `rag-search "<query>"` to check local files when the question may be about
   projects, settings, scripts, logs, or downloaded files.
3. `memory-first-search "<query>"` when web search is still needed; it checks
   memory first, searches through SearXNG if needed, then stores useful results.
4. `remember-note "Fact: ..."` after learning stable information that should be
   available in future sessions.

Store only durable facts, not every transient command output. Good candidates:
working commands, model IDs, LM Studio/OpenHands settings, installed tools,
fixed errors, project locations, API endpoint choices, and user preferences.

Important local paths and commands:

- MemPalace palace: `/mnt/Debian/home/vojtech/.mempalace/palace`
- Memory search: `memory-search "<query>"`
- Memory-first web search: `memory-first-search "<query>"`
- Save note: `remember-note "Fact: ..."`
- Local file search/RAG: `rag-search "<query>"`
- SearXNG search: `searxng-search "<query>"`
