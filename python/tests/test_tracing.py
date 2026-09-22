"""Exercise optional tracing with and without the actual SDK installed."""

import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from judge_jev.logfire_tracing import TracingConfig, configure_tracing

REPO = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("region", ["eu", "us"])
def test_configure_tracing_uses_supported_token_configuration(monkeypatch, region):
    calls = []

    # Explicit keywords prevent an unsupported SDK argument from going unnoticed.
    def configure(*, token, service_name, service_version, environment,
                  send_to_logfire, console, advanced):
        calls.append((token, service_name, send_to_logfire, console, advanced.base_url))

    monkeypatch.setitem(sys.modules, "logfire", SimpleNamespace(
        configure=configure, AdvancedOptions=SimpleNamespace,
    ))
    monkeypatch.setenv("JUDGE_JEV_LOGFIRE_TOKEN", " test-write-token ")
    monkeypatch.setenv("JUDGE_JEV_LOGFIRE_REGION", region)
    monkeypatch.setenv("JUDGE_JEV_LOGFIRE_PROJECT", "ignored-project")
    assert configure_tracing(TracingConfig(enabled=True, sink="logfire"))
    assert calls == [("test-write-token", "judge-jev", True, False,
                      f"https://logfire-{region}.pydantic.dev")]


def test_missing_sdk_with_token_is_noop(monkeypatch):
    monkeypatch.setenv("JUDGE_JEV_LOGFIRE_TOKEN", "test-write-token")
    monkeypatch.setitem(sys.modules, "logfire", None)
    assert not configure_tracing(TracingConfig(enabled=True, sink="logfire"))


def test_real_sdk_emits_spans_and_preserves_json_stdout(tmp_path):
    pytest.importorskip("logfire")
    # A separate process keeps the SDK's global configuration out of other tests.
    # Run the real configuration and spans, replacing only remote export.
    script = tmp_path / "trace_smoke.py"
    script.write_text('''
import sys
import logfire
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from judge_jev.cli import main

exporter = InMemorySpanExporter()
real_configure = logfire.configure
def offline_configure(**kwargs):
    assert kwargs["send_to_logfire"] is True
    kwargs["send_to_logfire"] = False
    kwargs["metrics"] = False
    kwargs["inspect_arguments"] = False
    kwargs["additional_span_processors"] = [SimpleSpanProcessor(exporter)]
    return real_configure(**kwargs)
logfire.configure = offline_configure

code = main(["run", "--rubric", "assistant-reply", "--input", sys.argv[1],
             "--mock", "--tracing", "--tracing-to", "logfire"])
assert code == 0
names = {span.name for span in exporter.get_finished_spans()}
assert {"judge.run", "gates", "jev.system_one", "route"} <= names, names
''')
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("LOGFIRE_", "JUDGE_JEV_LOGFIRE_", "OTEL_"))}
    env.update(JUDGE_JEV_LOGFIRE_TOKEN="test-write-token", JUDGE_JEV_ROOT=str(REPO))
    result = subprocess.run(
        [sys.executable, str(script), str(REPO / "fixtures/assistant-reply-pass.json")],
        env=env, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["verdict"] == "pass"
