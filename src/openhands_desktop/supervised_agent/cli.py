"""Command-line entry point.

Usage:
    python -m supervised_agent.cli --workspace /path/to/project --task "..."
"""

from __future__ import annotations

import argparse
import json
import sys

from openhands_desktop.supervised_agent.llm_client import LlmClient
from openhands_desktop.supervised_agent.run_agent import run_agent

DEFAULT_MODEL = "qwen3.6-35b-a3b-iq4_nl"
DEFAULT_BASE_URL = "http://127.0.0.1:1234/v1"


def main() -> int:
    parser = argparse.ArgumentParser(description="Supervised local coding agent")
    parser.add_argument("--workspace", required=True, help="Directory the agent may read/write")
    parser.add_argument("--task", required=True, help="Task description")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--api-key", default=None)
    args = parser.parse_args()

    llm = LlmClient(model=args.model, base_url=args.base_url, api_key=args.api_key)

    def on_step(event: dict) -> None:
        kind = event.get("kind")
        step = event.get("step")
        if kind == "action":
            action = event["action"]
            print(f"[step {step}] action={action.get('action')} reason={action.get('reason', '')[:150]}")
        elif kind == "nudge":
            print(f"[step {step}] NUDGE: repeated call to {event['tool']} -- redirecting model")
        elif kind == "tool_result":
            status = "ok" if event["ok"] else "FAILED"
            print(
                f"[step {step}] tool={event['tool']} ({status}, progress={event['progress']:+d}): "
                f"{event['output_preview'][:200]}"
            )
        elif kind == "verifier_rejected":
            print(f"[step {step}] verifier rejected FINISH: {event['reason']}")
        sys.stdout.flush()

    try:
        result = run_agent(args.task, args.workspace, llm, on_step=on_step)
    finally:
        llm.close()

    print("\n=== RESULT ===")
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result.get("status") == "COMPLETED" else 1


if __name__ == "__main__":
    sys.exit(main())
