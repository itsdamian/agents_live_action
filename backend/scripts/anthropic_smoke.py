"""Manual smoke test for the real Anthropic (Claude) provider (A7, AC7).

Makes ONE real API call -- it costs a little money -- so it is for you to run by
hand, never from pytest or CI::

    cd backend
    read -rs ANTHROPIC_API_KEY && export ANTHROPIC_API_KEY   # keeps the key out of history
    python scripts/anthropic_smoke.py [--model claude-sonnet-5-5]

This script does NOT read ``.env`` (only docker compose's env_file does). To load
the repo's ``.env`` into your current shell first: ``set -a; source ../.env; set +a``
(run from ``backend/``).

Prints the first 200 characters of the reply, ``stop_reason``, the model that
answered, token usage and the estimated cost. With no key it prints how to set
one and exits with status 2 without calling anything. The key itself is never
printed.

Exit codes: 0 ok, 1 the call failed or its output was unusable, 2 no API key.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # make ``core`` importable

from config import _is_set  # noqa: E402
from core.llm_client import AnthropicClient  # noqa: E402
from core.usage_tracker import estimate_cost  # noqa: E402

DEFAULT_MODEL = "claude-opus-5-5"
PROMPT = "Reply with one short sentence confirming you can read this message."


class _RecordingSDK:
    """Delegates to a real SDK client and keeps the last raw response for printing."""

    def __init__(self, sdk) -> None:
        self._sdk = sdk
        self.last_response = None
        self.beta = type("Beta", (), {"messages": self._namespace(sdk.beta.messages)})()
        self.messages = self._namespace(sdk.messages)

    def _namespace(self, target):
        recorder = self

        class _Ns:
            @staticmethod
            def create(**kwargs):
                recorder.last_response = target.create(**kwargs)
                return recorder.last_response

        return _Ns()


def _build_real_sdk(api_key: str):
    import anthropic  # only reached when a key is set and a real call is intended

    return anthropic.Anthropic(api_key=api_key, max_retries=0)


def main(argv: list[str] | None = None, *, sdk_factory=_build_real_sdk) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--api-key-env", default="ANTHROPIC_API_KEY")
    args = parser.parse_args(argv)

    api_key = os.environ.get(args.api_key_env)
    if not _is_set(api_key):
        print(
            f"No API key: environment variable {args.api_key_env} is not set.\n"
            f"Set it in this shell and run the script again, e.g. "
            f"`read -rs {args.api_key_env} && export {args.api_key_env}`.\n"
            "Note: this script does not read .env. To load the repo's .env into this shell "
            "(from backend/): `set -a; source ../.env; set +a`.\n"
            "Nothing was sent.",
            file=sys.stderr,
        )
        return 2

    recording = _RecordingSDK(sdk_factory(api_key))
    client = AnthropicClient(args.model, args.api_key_env, sdk_client=recording)
    try:
        reply = client.call(PROMPT, "You are a connectivity check. Be brief.")
    except Exception as exc:  # noqa: BLE001 - show any failure, never the key
        print(f"Call failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    response = recording.last_response
    usage = client.last_usage
    cost = estimate_cost("anthropic", args.model, usage.input_tokens, usage.output_tokens) if usage else None
    print(f"reply (first 200 chars): {reply[:200]!r}")
    print(f"stop_reason: {getattr(response, 'stop_reason', None)}")
    print(f"model: {getattr(response, 'model', args.model)} (requested: {args.model})")
    if usage:
        print(f"usage: input_tokens={usage.input_tokens} output_tokens={usage.output_tokens}")
        print(f"estimated cost: ${cost:.6f} (list-price estimate; a fallback model may bill differently)")
    else:
        print("usage: not reported")
    return 0


if __name__ == "__main__":
    sys.exit(main())
