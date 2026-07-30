---
name: ask-the-user
type: knowledge
version: 1.0.0
agent: CodeActAgent
triggers:
- which approach
- should i
- do you want
- prefer
- ambiguous
- unclear
- not sure
- confirm
---

# Asking the user instead of guessing

When the OpenHands desktop app is running it exposes a tool named
`openhands-desktop-ask-user_ask_user_question`. It shows the user a dialog with
clickable options and returns the one they chose, or text they typed.

Use it. The base instructions repeatedly say to "consult with the user" or
"confirm with the user before proceeding", but writing that intention as prose
only works if a human happens to be reading — the run just ends. The tool is the
only way to actually get an answer mid-run.

## When to call it

- The decision is genuinely the user's: which of two valid designs to take,
  which file is authoritative when two disagree, whether a breaking change is
  acceptable.
- You are about to do something hard to undo (deleting files, force-pushing,
  dropping data, rewriting large amounts of working code).
- The request is ambiguous in a way that changes the outcome, and guessing wrong
  wastes the whole run.
- You have tried something twice and are about to try a third different guess.

## When NOT to call it

- You can answer it yourself by reading a file, running a command, or checking
  configuration. Look first — asking should never be a substitute for checking.
- It is a small reversible detail (a variable name, formatting, ordering).
- You already asked something nearly identical in this conversation.

## How to ask well

Give 2–4 short, mutually exclusive options. Put your recommendation first and
say why in the option text. Bad: "How should I proceed?" with no options.
Good: "Two files define this endpoint and they disagree. Which is authoritative?"
with options naming each file and what taking it implies.

If the tool is not available (the desktop app is not running), do not stall:
state the ambiguity plainly, pick the most defensible option, say which one you
picked and why, and continue.
