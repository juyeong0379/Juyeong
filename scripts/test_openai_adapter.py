"""Offline smoke test: OpenAI adapter, JSON shape and safe missing-key behavior.

This test never calls an external API and never spends tokens.
"""
import os
from types import SimpleNamespace

import generate_briefing as briefing


class FakeResponses:
    def __init__(self):
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(
            output_text='[{"index": 0, "is_relevant": true, "summary": ["핵심 사실", "주요 배경", "사업 영향"], "insight": ""}]'
        )


def run():
    saved = os.environ.pop("OPENAI_API_KEY", None)
    try:
        briefing._openai_client = None
        try:
            briefing.ask_openai_json("test", max_tokens=500)
            raise AssertionError("Missing key should raise RuntimeError")
        except RuntimeError as exc:
            assert "OPENAI_API_KEY" in str(exc)

        os.environ["OPENAI_API_KEY"] = "test-only-not-a-real-api-key"
        dummy = FakeResponses()
        briefing._openai_client = SimpleNamespace(responses=dummy)
        parsed = briefing.ask_openai_json("test", max_tokens=500)
        assert isinstance(parsed, list) and parsed[0]["is_relevant"] is True
        assert len(parsed[0]["summary"]) == 3
        assert dummy.kwargs["model"] == briefing.MODEL
        assert dummy.kwargs["store"] is False
        assert dummy.kwargs["max_output_tokens"] == 500
        print("PASS: OpenAI adapter JSON parsing and missing-key protection")
    finally:
        briefing._openai_client = None
        if saved is None:
            os.environ.pop("OPENAI_API_KEY", None)
        else:
            os.environ["OPENAI_API_KEY"] = saved


if __name__ == "__main__":
    run()
