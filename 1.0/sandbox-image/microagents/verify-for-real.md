---
name: verify-for-real
type: knowledge
version: 1.0.0
agent: CodeActAgent
triggers:
- works
- fixed
- done
- verify
- test
- it should
- try
- overiť
- funguje
- opravené
---

# Claim nothing you have not observed

A change is not finished when it is written. It is finished when you have run it
and seen the result. Reporting "this should now work" after only editing code is
the single most common way to waste the user's time — they then run it, it
fails, and the whole loop repeats.

## The rule

Before saying something works, produce evidence:

- Code change → run it, or run the test that covers it.
- Bug fix → reproduce the failure first if you can, then show it no longer
  reproduces. A fix you never saw fail is a guess.
- Config change → read the value back from the system that consumes it, not
  from the file you just wrote. Storage and effect are different things.
- Command → show the actual output, not a description of the expected output.

## Reporting

Say what you ran and what came back. If a check failed, say so plainly with the
output — a failing result reported honestly is far more useful than a success
you cannot back up.

If you could not verify something (no test infrastructure, needs a GPU, needs
credentials, takes an hour), say exactly that and say which part is unverified.
Never let an unverified step ride along inside an otherwise-confident summary.

## Distinguishing storage from effect

Especially with configuration: a setting that saves successfully, reads back
correctly, and appears in the UI can still have no effect on the running system.
The write path and the read path of the component that actually consumes it are
different things. When a setting matters, check it where it is used.
