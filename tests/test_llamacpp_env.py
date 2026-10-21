from __future__ import annotations

import pytest
from llamacpp.config import parse_env_text, extra_env
import config as app_config


def test_parse_env_text_dict():
    raw = {
        "GGML_CUDA_REGISTER_HOST": "1",
        "GGML_OP_OFFLOAD_MIN_BATCH": 64,
        "INVALID-NAME": "skip_me",
    }
    parsed = parse_env_text(raw)
    assert parsed == {
        "GGML_CUDA_REGISTER_HOST": "1",
        "GGML_OP_OFFLOAD_MIN_BATCH": "64",
    }


def test_parse_env_text_list():
    raw = [
        ["GGML_CUDA_REGISTER_HOST", "1"],
        ["GGML_OP_OFFLOAD_MIN_BATCH", "64"],
        "LLAMA_ATTN_ROT_DISABLE=1",
        ["INVALID-NAME", "skip"],
    ]
    parsed = parse_env_text(raw)
    assert parsed == {
        "GGML_CUDA_REGISTER_HOST": "1",
        "GGML_OP_OFFLOAD_MIN_BATCH": "64",
        "LLAMA_ATTN_ROT_DISABLE": "1",
    }


def test_parse_env_text_multiline_string():
    raw = """
    # comment
    GGML_CUDA_REGISTER_HOST=1
    GGML_OP_OFFLOAD_MIN_BATCH="64"
    LLAMA_ATTN_ROT_DISABLE='1'
    """
    parsed = parse_env_text(raw)
    assert parsed == {
        "GGML_CUDA_REGISTER_HOST": "1",
        "GGML_OP_OFFLOAD_MIN_BATCH": "64",
        "LLAMA_ATTN_ROT_DISABLE": "1",
    }


def test_parse_env_text_space_separated_string():
    raw = "GGML_CUDA_REGISTER_HOST=1 GGML_OP_OFFLOAD_MIN_BATCH=64"
    parsed = parse_env_text(raw)
    assert parsed == {
        "GGML_CUDA_REGISTER_HOST": "1",
        "GGML_OP_OFFLOAD_MIN_BATCH": "64",
    }


def test_extra_env_merge(monkeypatch):
    test_settings = {
        "llamacpp": {
            "env": {
                "GLOBAL_VAR_1": "global_val_1",
                "SHARED_VAR": "from_global",
            },
            "models": {
                "test-model": {
                    "env": {
                        "MODEL_VAR_1": "model_val_1",
                        "SHARED_VAR": "from_model",
                    }
                },
                "global-only-model": {},
            }
        }
    }

    monkeypatch.setattr(app_config, "get_nested", lambda path, default=None: (
        test_settings["llamacpp"]["env"] if path == "llamacpp.env"
        else test_settings["llamacpp"]["models"] if path == "llamacpp.models"
        else test_settings.get("llamacpp") if path == "llamacpp"
        else default
    ))

    # For a model with both global and model env
    merged = extra_env("test-model")
    assert merged == {
        "GLOBAL_VAR_1": "global_val_1",  # inherited from global
        "MODEL_VAR_1": "model_val_1",    # added by model
        "SHARED_VAR": "from_model",      # model overrides global
    }

    # For a model without its own env, inherits global
    global_only = extra_env("global-only-model")
    assert global_only == {
        "GLOBAL_VAR_1": "global_val_1",
        "SHARED_VAR": "from_global",
    }


def test_inference_for_model_overrides(monkeypatch):
    from llamacpp.config import inference_for

    test_settings = {
        "llamacpp": {
            "inference": {
                "context_overflow": "truncate_middle",
                "drop_prior_thinking": True,
            },
            "models": {
                "custom-model": {
                    "inference_defaults": {
                        "context_overflow": "error",
                        "drop_prior_thinking": False,
                    }
                },
                "default-model": {}
            }
        }
    }

    monkeypatch.setattr(app_config, "get_nested", lambda path, default=None: (
        test_settings["llamacpp"]["inference"] if path == "llamacpp.inference"
        else test_settings["llamacpp"]["models"] if path == "llamacpp.models"
        else test_settings.get("llamacpp") if path == "llamacpp"
        else default
    ))

    custom = inference_for("custom-model")
    assert custom["context_overflow"] == "error"
    assert custom["drop_prior_thinking"] is False

    default_inf = inference_for("default-model")
    assert default_inf["context_overflow"] == "truncate_middle"
    assert default_inf["drop_prior_thinking"] is True

def test_head_subset_argv_generation(monkeypatch, tmp_path):
    from llamacpp.argv import build_argv
    from llamacpp import config as lcfg

    dummy_ids = tmp_path / "tokens.txt"
    dummy_ids.write_text("1\n2\n3\n", encoding="utf-8")

    test_settings = {
        "llamacpp": {
            "models": {
                "dense-32b": {
                    "path": "models/qwen2.5-32b.gguf",
                    "head_subset": 32768,
                    "head_subset_ids": str(dummy_ids),
                },
                "mtp-model": {
                    "path": "models/qwen2.5-mtp.gguf",
                    "head_subset": 32768,
                    "spec_draft_head_subset": 16384,
                }
            }
        }
    }

    monkeypatch.setattr(app_config, "get_nested", lambda path, default=None: (
        test_settings["llamacpp"]["models"] if path == "llamacpp.models"
        else test_settings.get("llamacpp") if path == "llamacpp"
        else default
    ))
    monkeypatch.setattr(lcfg, "binary", lambda: "llama-server")

    # 1. Standalone dense model without MTP: produces --head-subset and --head-subset-ids
    argv_dense = build_argv("dense-32b")
    assert "--head-subset" in argv_dense
    idx = argv_dense.index("--head-subset")
    assert argv_dense[idx + 1] == "32768"
    assert "--head-subset-ids" in argv_dense
    idx_ids = argv_dense.index("--head-subset-ids")
    assert argv_dense[idx_ids + 1] == str(dummy_ids)
    assert "--spec-draft-head-subset" not in argv_dense

    # 2. Model with both base head_subset and speculative draft override
    argv_mtp = build_argv("mtp-model")
    assert "--head-subset" in argv_mtp
    assert argv_mtp[argv_mtp.index("--head-subset") + 1] == "32768"
    assert "--spec-draft-head-subset" in argv_mtp
    assert argv_mtp[argv_mtp.index("--spec-draft-head-subset") + 1] == "16384"

def test_runtime_layer_pruning_automatic(tmp_path):
    import numpy as np
    import sys
    sys.path.insert(0, "build/llama.cpp/gguf-py")
    import gguf
    from llamacpp.runtime_prune import ensure_runtime_pruned_model

    src_model = tmp_path / "base.gguf"
    w = gguf.GGUFWriter(str(src_model), "llama")
    w.add_block_count(8)
    for i in range(8):
        w.add_tensor(f"blk.{i}.weight", np.array([float(i)], dtype=np.float32))
    w.add_tensor("output.weight", np.array([1.0], dtype=np.float32))
    w.write_header_to_file()
    w.write_kv_data_to_file()
    w.write_tensors_to_file()
    w.close()

    # Disabled: passes through original
    assert ensure_runtime_pruned_model(str(src_model), {}) == str(src_model)

    # Enabled: automatically creates cached pruned model with 6 layers
    pruned_path = ensure_runtime_pruned_model(str(src_model), {"runtime_layer_prune": 0.75})
    assert pruned_path != str(src_model)
    r = gguf.GGUFReader(pruned_path)
    assert int(r.get_field("llama.block_count").contents()) == 6
