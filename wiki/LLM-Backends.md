# LLM Backend

The Raspberry Pi deployment uses one backend: `llama-cpp-python` loading a local
GGUF model in-process. There is no external AI API, model server, Ollama service,
or HuggingFace transformers backend.

---

## Why llama.cpp

- GGUF quantisation lets a 3B-8B model fit on a Pi 5.
- ARM NEON and OpenBLAS provide the best CPU path available on the Pi.
- Tool calls run through the same local process as the FastAPI app.
- The model file is bind-mounted from `./models` and never leaves the device.

---

## Recommended models

| Model key | Approx size | Use |
| --- | ---: | --- |
| `qwen2.5-7b` | 4.7 GB | Stable tool-calling quality/speed trade-off on an 8 GB Pi 5 |
| `qwen2.5-3b` | 3.3 GB | More memory headroom, faster responses |
| `llama3.2-3b` | 3.4 GB | Lightweight fallback |
| `mistral-7b` | 4.4 GB | Solid general model |

Download:

```bash
python3 scripts/download_model.py --list
python3 scripts/download_model.py qwen2.5-7b --output-dir ./models
```

Then set:

```env
LLM_MODEL_PATH=/app/models/qwen2.5-7b-instruct-q4_k_m.gguf
LLM_CONTEXT_SIZE=4096
LLM_N_THREADS=4
LLM_N_BATCH=128
AGENT_MAX_TOKENS=2048
AGENT_TEMPERATURE=0.1
```

## Fine-tuning decision

Fine-tuning is not part of the Pi runtime. A fine-tuned model would still need
fresh prices, filings, news, portfolio state, and broker constraints; training
on historical recommendations can also bake in stale facts and hindsight
bias. The safer and more maintainable approach is a general instruct model,
strict tool schemas, deterministic server-side trade limits, persistent news
memory, and backtests/paper trading. If a later dataset justifies adaptation,
train and evaluate it off-device, export a quantised GGUF, and validate tool
calling and risk controls on the Pi before replacing the model.

The selected GGUF must include a tool-aware chat template. If the model emits
plain text instead of structured tool calls, inspect its template metadata or
choose a model with native function-calling support; do not let the assistant
execute trades through text parsing.

## Current model decision (August 2026)

The repository remains on Qwen2.5-7B-Instruct Q4_K_M. The reason is runtime compatibility,
not a claim that it wins every general benchmark: the current llama.cpp function-calling
path explicitly supports Qwen 2.5 templates, and the existing Pi deployment already budgets
for its quantized memory footprint. The server-side safety layer remains authoritative even
when the model is wrong.

Qwen3.5-4B is a promising future candidate with strong official reasoning/tool-use results,
but its documented serving examples use Qwen-specific SGLang/vLLM parsers rather than the
current in-process llama.cpp path. It must pass a Pi A/B test for tool-call validity, latency,
RAM, and refusal/safety cases before migration. Phi-4-mini is lighter, but its official card
reports weaker MMLU/MMLU-Pro comparisons against Qwen2.5-7B and warns that function calling
can hallucinate names or URLs. Fine-tuning is not currently justified: fresh prices, news,
portfolio state, and deterministic server guards are more valuable than memorizing historical
investment text. If fine-tuning is later attempted, train off-device and require held-out
tool-call and risk-control tests before exporting a GGUF.
