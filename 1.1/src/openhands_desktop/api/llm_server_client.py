"""Talks directly to the LLM server itself (e.g. LM Studio), not the OpenHands
agent-server -- a separate concern from AppServerClient, which only knows
about port 3000. Used to detect which model is actually loaded right now, so
the UI can stay honest about it instead of just trusting whatever profile
was last selected (confirmed live 2026-07-28: LM Studio auto-swaps the
loaded model on demand, so the "active" profile in the app can silently
drift out of sync with reality).
"""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

import httpx

DEFAULT_LLM_SERVER_BASE_URLS = (
    "http://127.0.0.1:1234/v1",
    "http://localhost:1234/v1",
    "http://172.17.0.1:1234/v1",
)
_LOCAL_SERVER_HOSTS = {"127.0.0.1", "localhost", "0.0.0.0", "172.17.0.1"}
_LMS = "/home/vojtech/.lmstudio/bin/lms"
_MODEL_LOAD_SPECS = {
    "27b": {
        "key": (
            "hauhaucs/qwen3.6-27b-uncensored-hauhaucs-aggressive/"
            "qwen3.6-27b-uncensored-hauhaucs-aggressive-q4_k_p.gguf"
        ),
        "identifier": "lmstudio-qwen36-27b-q4",
    },
    "35b": {
        "key": "qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive@iq4_nl",
        "identifier": "qwen3.6-35b-a3b-iq4_nl",
    },
}


@dataclass(frozen=True)
class LoadedModel:
    model_id: str
    # Address stored in an OpenHands profile. Local loopback addresses are
    # translated to the Docker host gateway because the LLM request is made
    # from the sandbox container, not from this desktop process.
    base_url: str
    aliases: tuple[str, ...] = ()
    detected_base_url: str | None = None


def _server_root(base_url: str) -> str:
    root = base_url.rstrip("/")
    if root.endswith("/v1"):
        root = root[: -len("/v1")]
    return root


def profile_base_url(base_url: str) -> str:
    """Return a URL that both identifies the server and works in sandboxes."""
    parsed = urlsplit(base_url.rstrip("/"))
    host = parsed.hostname or ""
    if host not in _LOCAL_SERVER_HOSTS:
        return base_url.rstrip("/")
    port = f":{parsed.port}" if parsed.port is not None else ""
    netloc = f"172.17.0.1{port}"
    path = parsed.path or "/v1"
    return urlunsplit((parsed.scheme or "http", netloc, path, "", ""))


def same_server(left: str | None, right: str | None) -> bool:
    if not left or not right:
        return False
    return profile_base_url(left).rstrip("/").lower() == profile_base_url(right).rstrip("/").lower()


async def get_loaded_model_id(base_url: str) -> str | None:
    """Uses LM Studio's own extended `/api/v0/models` endpoint (not standard
    OpenAI-compatible -- confirmed live) which reports a `state` field
    ("loaded" vs "not-loaded") per model, unlike the plain `/v1/models`
    endpoint which lists every downloaded model regardless of whether it's
    actually in memory. `base_url` is expected to look like
    "http://host:port/v1"; the `/v0` root is a sibling of `/v1`, not nested
    under it.
    """
    detected = await detect_loaded_model((base_url,))
    return detected.model_id if detected else None


async def detect_loaded_model(base_urls: tuple[str, ...] | None = None) -> LoadedModel | None:
    """Find a running LM Studio-compatible server and its loaded model.

    When `base_urls` is omitted, probes the common local LM Studio addresses.
    Returned `base_url` always points at the OpenAI-compatible `/v1` endpoint
    because that is what OpenHands stores in LLM profiles.
    """
    candidates = base_urls or DEFAULT_LLM_SERVER_BASE_URLS
    async with httpx.AsyncClient(timeout=5.0) as client:
        for base_url in candidates:
            root = _server_root(base_url)
            try:
                resp = await client.get(f"{root}/api/v0/models")
                resp.raise_for_status()
            except httpx.HTTPError:
                continue
            for entry in resp.json().get("data", []):
                if entry.get("state") == "loaded" and entry.get("id"):
                    return LoadedModel(
                        model_id=entry["id"],
                        base_url=profile_base_url(f"{root}/v1"),
                        aliases=(entry["id"],),
                        detected_base_url=f"{root}/v1",
                    )

    if base_urls:
        return None
    # Blocks on spawning the `lms` CLI process (genuinely can take over a
    # second) -- confirmed live 2026-07-31 as a cause of the whole GUI
    # freezing briefly: this runs on the qasync event loop (the same one
    # that pumps Qt's own UI events) every _model_sync_timer tick (15s)
    # whenever LM Studio's REST endpoint doesn't answer immediately, e.g.
    # while it's busy mid-generation. asyncio.to_thread moves the blocking
    # subprocess call off that loop.
    return await asyncio.to_thread(_detect_loaded_model_from_lms_cli)


async def probe_llm_server_state(base_urls: tuple[str, ...] | None = None) -> str:
    """"loaded" / "no_model" / "unreachable".

    detect_loaded_model() collapses "server reachable but nothing loaded"
    and "nothing running at all" into the same `None` -- a real gap found
    live 2026-07-30: three conversations failed immediately with
    litellm.BadRequestError "No models loaded", and nothing in the UI had
    warned about it beforehand (the health chip only checks the OpenHands
    agent-server on port 3000, not LM Studio on 1234, so it stayed green).
    This tells those two cases apart so the caller can warn specifically
    about the empty one instead of staying silent."""
    candidates = base_urls or DEFAULT_LLM_SERVER_BASE_URLS
    async with httpx.AsyncClient(timeout=5.0) as client:
        for base_url in candidates:
            root = _server_root(base_url)
            try:
                resp = await client.get(f"{root}/api/v0/models")
                resp.raise_for_status()
            except httpx.HTTPError:
                continue
            entries = resp.json().get("data", [])
            return "loaded" if any(e.get("state") == "loaded" for e in entries) else "no_model"
    return "unreachable"


def _detect_loaded_model_from_lms_cli() -> LoadedModel | None:
    lms = shutil.which("lms") or _LMS
    try:
        proc = subprocess.run(
            [lms, "ps", "--json"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        models = json.loads(proc.stdout or "[]")
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return None
    if not models:
        return None
    aliases = tuple(
        str(value)
        for value in (
            models[0].get("identifier"),
            models[0].get("modelKey"),
            models[0].get("indexedModelIdentifier"),
            models[0].get("path"),
            models[0].get("displayName"),
        )
        if value
    )
    model_id = aliases[0] if aliases else None
    if not model_id:
        return None
    return LoadedModel(
        model_id=model_id,
        base_url=profile_base_url(DEFAULT_LLM_SERVER_BASE_URLS[0]),
        aliases=aliases,
        detected_base_url=DEFAULT_LLM_SERVER_BASE_URLS[0],
    )


_PLAN_OR_CODE_SCHEMA = {
    "type": "object",
    "properties": {"mode": {"type": "string", "enum": ["plan", "code"]}},
    "required": ["mode"],
}


async def classify_plan_or_code(base_url: str, model: str, task: str) -> str:
    """One cheap classification call, borrowed from how Claude Code itself
    judges whether a request needs a plan before touching anything: ambiguous
    or multi-step work gets PLAN, a single well-defined change gets CODE.
    Defaults to "code" (today's existing default) on any failure -- a wrong
    guess here should degrade to today's behavior, not block sending."""
    root = _server_root(base_url)
    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "Classify the user's task. Reply 'plan' if it is ambiguous, "
                    "spans multiple files/steps, or needs research/design before "
                    "any change should be made. Reply 'code' if it is a single, "
                    "well-defined change that can start immediately."
                ),
            },
            {"role": "user", "content": task[:4000]},
        ],
        "max_tokens": 200,
        "temperature": 0.0,
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "plan_or_code", "strict": True, "schema": _PLAN_OR_CODE_SCHEMA},
        },
    }
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(f"{root}/v1/chat/completions", json=payload)
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"].get("content") or ""
            mode = json.loads(content).get("mode")
            return mode if mode in ("plan", "code") else "code"
    except (httpx.HTTPError, ValueError, KeyError, IndexError):
        return "code"


def model_variant(profile_name: str, model: str) -> str | None:
    value = f"{profile_name} {model}".lower()
    if "35b" in value:
        return "35b"
    if "27b" in value:
        return "27b"
    return None


DEFAULT_LM_STUDIO_CONTEXT_LENGTH = 131072


async def ensure_profile_model_loaded(
    profile_name: str, model: str, context_length: int = DEFAULT_LM_STUDIO_CONTEXT_LENGTH
) -> None:
    """Ensure the known local Plan/Code profile is physically loaded.

    `context_length` only takes effect on the local fallback load path
    (_load_model_with_lms) -- the http://127.0.0.1:8899/swap helper, when
    reachable, decides context length itself and isn't told this value.
    """
    variant = model_variant(profile_name, model)
    if variant is None:
        return
    target = _MODEL_LOAD_SPECS[variant]
    detected = await detect_loaded_model()
    if detected is not None and _matches_identifier(detected, target["identifier"]):
        return

    helper_error: Exception | None = None
    try:
        async with httpx.AsyncClient(timeout=330.0) as client:
            response = await client.get(f"http://127.0.0.1:8899/swap?model={variant}")
            response.raise_for_status()
            payload = response.json()
            if not payload.get("ok"):
                raise RuntimeError(payload.get("detail") or "LM Studio model swap failed")
    except (httpx.HTTPError, ValueError, RuntimeError) as exc:
        helper_error = exc
        await asyncio.to_thread(_load_model_with_lms, target["key"], target["identifier"], context_length)

    detected = await detect_loaded_model()
    if detected is None or not _matches_identifier(detected, target["identifier"]):
        # An older helper may report success while loading a different
        # quantization/identifier. Correct that locally instead of leaving the
        # selected profile and physical model out of sync.
        if helper_error is None:
            await asyncio.to_thread(_load_model_with_lms, target["key"], target["identifier"], context_length)
            detected = await detect_loaded_model()
    if detected is None or not _matches_identifier(detected, target["identifier"]):
        detail = f": {helper_error}" if helper_error is not None else ""
        raise RuntimeError(f"LM Studio did not load {target['identifier']}{detail}")


def _matches_identifier(detected: LoadedModel, identifier: str) -> bool:
    wanted = identifier.strip().lower()
    return any(str(alias).strip().lower() == wanted for alias in detected.aliases or (detected.model_id,))


def _load_model_with_lms(
    model_key: str, identifier: str, context_length: int = DEFAULT_LM_STUDIO_CONTEXT_LENGTH
) -> None:
    lms = shutil.which("lms") or _LMS
    subprocess.run(
        [lms, "server", "start", "--port", "1234", "--bind", "0.0.0.0"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    subprocess.run(
        [lms, "unload", "--all"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    # 2026-07-31: this used to be hardcoded at 32768, well below what the
    # profiles' max_input_tokens/condenser.max_tokens actually expect (every
    # model swap the app itself triggers -- Plan/Code switch, Continue as
    # Code, manual profile activation -- goes through this function, not
    # openhands-plan-launch.sh's own --context-length 131072). A 49316-token
    # request got rejected by a model loaded here with only 32768 available.
    # Now a caller-supplied value (Settings -> LLM -> LM Studio context
    # length), not another hardcoded number.
    loaded = subprocess.run(
        [
            lms,
            "load",
            model_key,
            "--context-length",
            str(context_length),
            "--parallel",
            "1",
            "--gpu",
            "0.8",
            "--identifier",
            identifier,
            "-y",
        ],
        capture_output=True,
        text=True,
        timeout=330,
        check=False,
    )
    if loaded.returncode != 0:
        detail = (loaded.stdout + loaded.stderr).strip()[-2000:]
        raise RuntimeError(detail or f"Could not load {identifier}")
