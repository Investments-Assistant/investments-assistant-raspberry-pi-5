#!/usr/bin/env python3
"""Download a GGUF model from HuggingFace for the llama_cpp backend.

Models are saved to /app/models/ by default (override with --output-dir).
After downloading, set LLM_MODEL_PATH in your .env to the printed path.

Usage
-----
    python3 scripts/download_model.py --list             # show available presets
    python3 scripts/download_model.py qwen2.5-7b         # download by key
    python3 scripts/download_model.py                    # interactive picker
    python3 scripts/download_model.py qwen2.5-7b \\
        --output-dir ~/models                           # custom directory
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
import urllib.request

# ---------------------------------------------------------------------------
# Preset catalogue — verified to work with llama-cpp-python tool calling
# ---------------------------------------------------------------------------
PRESETS: dict[str, dict[str, str]] = {
    "qwen2.5-7b": {
        "repo": "Qwen/Qwen2.5-7B-Instruct-GGUF",
        "file": "qwen2.5-7b-instruct-q4_k_m.gguf",
        "size": "~4.7 GB",
        "notes": "Best quality/speed on Pi 5 (8 GB RAM). Native tool calling.",
    },
    "qwen2.5-3b": {
        "repo": "Qwen/Qwen2.5-3B-Instruct-GGUF",
        "file": "qwen2.5-3b-instruct-q8_0.gguf",
        "size": "~3.3 GB",
        "notes": "Lighter Qwen. Faster inference, slightly lower quality.",
    },
    "llama3.2-3b": {
        "repo": "bartowski/Llama-3.2-3B-Instruct-GGUF",
        "file": "Llama-3.2-3B-Instruct-Q8_0.gguf",
        "size": "~3.4 GB",
        "notes": "Good for testing or memory-constrained setups.",
    },
    "llama3.1-8b": {
        "repo": "bartowski/Meta-Llama-3.1-8B-Instruct-GGUF",
        "file": "Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf",
        "size": "~4.9 GB",
        "notes": "Strong instruction-following and tool use. Needs 8 GB RAM.",
    },
    "mistral-7b": {
        "repo": "TheBloke/Mistral-7B-Instruct-v0.3-GGUF",
        "file": "mistral-7b-instruct-v0.3.Q4_K_M.gguf",
        "size": "~4.4 GB",
        "notes": "Solid all-rounder. Good function calling support.",
    },
    "phi4-14b": {
        "repo": "bartowski/phi-4-GGUF",
        "file": "phi-4-Q4_K_M.gguf",
        "size": "~8.9 GB",
        "notes": "Microsoft Phi-4. Excellent reasoning. Needs GPU or 16+ GB RAM.",
    },
}

HF_BASE = "https://huggingface.co"


def list_presets() -> None:
    print("\nAvailable model presets:\n")
    col = max(len(k) for k in PRESETS) + 2
    print(f"  {'Key':{col}} {'Size':<12} Notes")
    print(f"  {'-' * col} {'-' * 12} {'-' * 52}")
    for key, info in PRESETS.items():
        print(f"  {key:{col}} {info['size']:<12} {info['notes']}")
    print()


def _progress_hook(count: int, block_size: int, total_size: int) -> None:
    if total_size <= 0:
        return
    pct = min(100, int(count * block_size * 100 / total_size))
    filled = pct // 2
    bar = "#" * filled + "-" * (50 - filled)
    print(f"\r  [{bar}] {pct:3d}%", end="", flush=True)


def download(key: str, output_dir: Path) -> Path:
    if key not in PRESETS:
        print(f"\nError: unknown model key '{key}'.")
        print("Run with --list to see available presets.")
        sys.exit(1)

    info = PRESETS[key]
    output_dir.mkdir(parents=True, exist_ok=True)
    dest = output_dir / info["file"]

    if dest.exists():
        size_gb = dest.stat().st_size / 1024**3
        print(f"\nModel already exists at {dest} ({size_gb:.1f} GB)")
        return dest

    url = f"{HF_BASE}/{info['repo']}/resolve/main/{info['file']}"
    print(f"\nDownloading {key}  ({info['size']})")
    print(f"  Source : {url}")
    print(f"  Target : {dest}\n")

    try:
        urllib.request.urlretrieve(url, dest, reporthook=_progress_hook)
    except Exception as exc:
        print(f"\n\nDownload failed: {exc}")
        if dest.exists():
            dest.unlink()
        sys.exit(1)

    print(f"\n\nDownload complete: {dest}")
    return dest


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Download GGUF models for the investment-assistant llama_cpp backend"
    )
    parser.add_argument("model", nargs="?", help="Model key (see --list for options)")
    parser.add_argument("--list", action="store_true", help="List available model presets")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("/app/models"),
        help="Directory to save the model file (default: /app/models)",
    )
    args = parser.parse_args()

    if args.list:
        list_presets()
        return

    if args.model:
        dest = download(args.model, args.output_dir)
        print(f"\nAdd to your .env:\n  LLM_MODEL_PATH={dest}\n")
        return

    # Interactive mode
    list_presets()
    key = input("Enter model key to download (or press Enter to cancel): ").strip()
    if not key:
        print("Cancelled.")
        return
    dest = download(key, args.output_dir)
    print(f"\nAdd to your .env:\n  LLM_MODEL_PATH={dest}\n")


if __name__ == "__main__":
    main()
