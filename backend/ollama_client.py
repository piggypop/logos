import sys

import httpx
import ollama as ol

# Short timeouts for non-streaming calls so the UI never hangs on an
# unreachable Ollama. Stream calls do their own (long) socket wait.
_DEFAULT_TIMEOUT = httpx.Timeout(connect=2.0, read=5.0, write=5.0, pool=5.0)


def _client(host: str) -> "ol.Client":
    """Returns an ollama Client with a sensible default HTTP timeout."""
    return ol.Client(host=host, timeout=_DEFAULT_TIMEOUT)


def stream_chat(
    messages: list[dict],
    model: str,
    system_prompt: str,
    host: str,
    temperature: float,
    num_ctx: int | None = None,
):
    """
    Generator that yields tokens as they arrive from Ollama
    (most models emit tokens at word / sub-word granularity).

    ``num_ctx`` is the Ollama context-window size. When None, Ollama uses
    its model default (often 2048), which causes intra-session amnesia in
    long conversations. The chat endpoint always passes the config value
    so the user can tune it from Settings.
    """
    full_messages = [{"role": "system", "content": system_prompt}] + messages
    # Streaming chat: long-running, use ollama's default (no read timeout).
    client = ol.Client(host=host)
    options: dict = {"temperature": temperature}
    if num_ctx is not None:
        options["num_ctx"] = num_ctx
    for chunk in client.chat(
        model=model,
        messages=full_messages,
        stream=True,
        options=options,
    ):
        text = chunk["message"]["content"]
        yield text
        sys.stdout.flush()


def list_models(host: str) -> list[str]:
    try:
        client = _client(host)
        return [m["model"] for m in client.list()["models"]]
    except Exception as e:
        print(f"[ollama_client] list_models error: {e}", file=sys.stderr)
        sys.stderr.flush()
        return []


def get_capabilities(host: str, model: str) -> list[str]:
    """
    Returns model capabilities (e.g. ['completion', 'tools', 'vision']).
    Empty list on error or unreachable host (with short timeout to keep UI snappy).
    """
    try:
        client = _client(host)
        info = client.show(model)
        caps = getattr(info, "capabilities", None)
        if caps is None and isinstance(info, dict):
            caps = info.get("capabilities", [])
        return list(caps or [])
    except Exception as e:
        print(f"[ollama_client] get_capabilities error: {e}", file=sys.stderr)
        sys.stderr.flush()
        return []
