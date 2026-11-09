"""Dev-only vLLM servers on Modal (chat + embeddings), OpenAI-compatible, API-key protected.

Setup (once):
    modal secret create vllm-api-key VLLM_API_KEY=<long-random-string>
Deploy:
    modal deploy infra/modal/vllm_app.py
Then set LLM_BASE_URL / EMBEDDING_BASE_URL to the printed URLs + "/v1".
"""

import os
import shutil
import subprocess

import modal

VLLM_VERSION = "0.13.0"
CHAT_MODEL = "Qwen/Qwen2.5-7B-Instruct"
CHAT_NAME = "qwen2.5-7b-instruct"
# The chat model decodes at memory-bandwidth speed, so the GPU sets the pace of every agent call. L4 (about
# 300 GB/s) made a benchmark scenario take about 130 s; A100-80GB has roughly seven times the bandwidth. Override at
# deploy time with CHAT_GPU (for example L40S or H100). Changing it needs `modal deploy`.
CHAT_GPU = os.environ.get("CHAT_GPU", "A100-80GB")
# Idle time before the chat container shuts down. A cold start takes about 8-10 minutes, so keep this longer than
# the gaps between benchmark runs. Override with CHAT_SCALEDOWN_SECONDS (Modal allows 2-3600).
CHAT_SCALEDOWN_SECONDS = int(os.environ.get("CHAT_SCALEDOWN_SECONDS", "900"))
# Containers kept running even when idle (costs GPU time continuously). Leave at 0 and use
# infra/modal/keep_warm.py to switch it on while iterating, without redeploying.
CHAT_MIN_CONTAINERS = int(os.environ.get("CHAT_MIN_CONTAINERS", "0"))
# Ungated mirror of meta-llama/Llama-3.1-8B-Instruct (the official repo needs an HF token).
JUDGE_MODEL = "NousResearch/Meta-Llama-3.1-8B-Instruct"
JUDGE_NAME = "llama-3.1-8b-instruct"
EMBED_MODEL = "BAAI/bge-base-en-v1.5"
EMBED_NAME = "bge-base-en-v1.5"
# Larger, third model family (Mistral) used only to simulate human rubric labels offline.
LABELLER_MODEL = "mistralai/Mistral-Small-24B-Instruct-2501"
LABELLER_NAME = "mistral-small-24b-instruct"

image = modal.Image.debian_slim(python_version="3.12").uv_pip_install(
    f"vllm=={VLLM_VERSION}", "huggingface-hub==0.36.0"
)

app = modal.App("sre-agent-vllm", image=image)
cache = modal.Volume.from_name("hf-cache", create_if_missing=True)
api_key = modal.Secret.from_name("vllm-api-key")
CACHE = {"/root/.cache/huggingface": cache}


def _serve(args: list[str], port: int) -> None:
    executable = shutil.which("vllm")
    if executable is None:
        raise RuntimeError("The vLLM executable is not available on PATH.")
    subprocess.Popen(  # noqa: S603 - fixed executable and argument list; shell execution is disabled.
        [
            executable,
            "serve",
            *args,
            "--host",
            "0.0.0.0",  # noqa: S104 - Modal must bind the web server on all interfaces.
            "--port",
            str(port),
        ]
    )


@app.function(
    gpu=CHAT_GPU,
    volumes=CACHE,
    secrets=[api_key],
    min_containers=CHAT_MIN_CONTAINERS,
    scaledown_window=CHAT_SCALEDOWN_SECONDS,
    timeout=3600,
)
@modal.concurrent(max_inputs=16)
@modal.web_server(port=8000, startup_timeout=1200)
def chat() -> None:
    _serve(
        [
            CHAT_MODEL,
            "--served-model-name",
            CHAT_NAME,
            "--max-model-len",
            "8192",
            "--enable-auto-tool-choice",
            "--tool-call-parser",
            "hermes",
        ],
        8000,
    )


@app.function(
    gpu="L4",
    volumes=CACHE,
    secrets=[api_key],
    scaledown_window=300,
    timeout=3600,
)
@modal.concurrent(max_inputs=8)
@modal.web_server(port=8000, startup_timeout=600)
def judge() -> None:
    """Different model family from the agents (Llama vs Qwen) so the LLM judge stays independent."""
    _serve(
        [JUDGE_MODEL, "--served-model-name", JUDGE_NAME, "--max-model-len", "8192"],
        8000,
    )


@app.function(
    gpu="A100-80GB",
    volumes=CACHE,
    secrets=[api_key],
    scaledown_window=120,
    timeout=3600,
)
@modal.concurrent(max_inputs=8)
@modal.web_server(port=8000, startup_timeout=1200)
def labeller() -> None:
    """Offline-only: simulates human rubric labels. Not part of the production path."""
    _serve(
        [LABELLER_MODEL, "--served-model-name", LABELLER_NAME, "--max-model-len", "8192"],
        8000,
    )


@app.function(
    gpu="T4",
    volumes=CACHE,
    secrets=[api_key],
    scaledown_window=300,
    timeout=3600,
)
@modal.concurrent(max_inputs=16)
@modal.web_server(port=8000, startup_timeout=600)
def embed() -> None:
    _serve(
        [EMBED_MODEL, "--served-model-name", EMBED_NAME, "--runner", "pooling", "--dtype", "half"],
        8000,
    )
