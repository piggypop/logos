import sys

import ollama as ol


def stream_chat(
    messages: list[dict], model: str, system_prompt: str, host: str, temperature: float
):
    """
    Generator που yield-άρει tokens όπως έρχονται από το Ollama
    (τα περισσότερα μοντέλα στέλνουν tokens ανά λέξη/υπολέξη).
    """
    full_messages = [{"role": "system", "content": system_prompt}] + messages
    client = ol.Client(host=host)
    for chunk in client.chat(
        model=model,
        messages=full_messages,
        stream=True,
        options={"temperature": temperature},
    ):
        text = chunk["message"]["content"]
        yield text
        sys.stdout.flush()


def list_models(host: str) -> list[str]:
    client = ol.Client(host=host)
    try:
        return [m["model"] for m in client.list()["models"]]
    except Exception:
        return []


def get_capabilities(host: str, model: str) -> list[str]:
    """
    Returns model capabilities (e.g. ['completion', 'tools', 'vision']).
    Empty list on error.
    """
    try:
        client = ol.Client(host=host)
        info = client.show(model)
        caps = getattr(info, "capabilities", None)
        if caps is None and isinstance(info, dict):
            caps = info.get("capabilities", [])
        return list(caps or [])
    except Exception as e:
        print(f"[ollama_client] get_capabilities error: {e}", file=sys.stderr)
        sys.stderr.flush()
        return []
