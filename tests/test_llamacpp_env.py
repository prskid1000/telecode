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
    import llamacpp.vram as lvram
    monkeypatch.setattr(lvram, "is_blackwell_gpu", lambda: False)

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


def test_blackwell_auto_register_host(monkeypatch):
    import llamacpp.vram as lvram
    monkeypatch.setattr(lvram, "is_blackwell_gpu", lambda: True)

    test_settings = {
        "llamacpp": {
            "env": {},
            "models": {
                "default-model": {},
                "opt-out-model": {
                    "env": {"GGML_CUDA_REGISTER_HOST": "0"}
                }
            }
        }
    }

    monkeypatch.setattr(app_config, "get_nested", lambda path, default=None: (
        test_settings["llamacpp"]["env"] if path == "llamacpp.env"
        else test_settings["llamacpp"]["models"] if path == "llamacpp.models"
        else test_settings.get("llamacpp") if path == "llamacpp"
        else default
    ))

    # 1. On Blackwell, GGML_CUDA_REGISTER_HOST=1 is automatically added
    env = extra_env("default-model")
    assert env.get("GGML_CUDA_REGISTER_HOST") == "1"

    # 2. User explicit opt-out is respected
    opt_out = extra_env("opt-out-model")
    assert opt_out.get("GGML_CUDA_REGISTER_HOST") == "0" 


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


def test_moe_threshold_and_layer_prune_env(monkeypatch):
    import llamacpp.vram as lvram
    monkeypatch.setattr(lvram, 'is_blackwell_gpu', lambda: False)

    test_settings = {
        'llamacpp': {
            'env': {},
            'models': {
                'moe-model': {
                    'moe_threshold': 0.02,
                },
                'dense-pruned': {
                    'layer_prune_ratio': 0.75,
                    'layer_drop_range': '16..23',
                },
            }
        }
    }

    def mock_get_nested(path, default=None):
        parts = path.split('.')
        curr = test_settings
        for p in parts:
            if isinstance(curr, dict) and p in curr:
                curr = curr[p]
            else:
                return default
        return curr
    monkeypatch.setattr(app_config, 'get_nested', mock_get_nested)

    env_moe = extra_env('moe-model')
    assert env_moe.get('GGML_CUDA_MOE_THRESHOLD') == '0.02'

    env_pruned = extra_env('dense-pruned')
    assert env_pruned.get('LLAMA_PRUNE_LAYERS') == '16..23'
    assert env_pruned.get('LLAMA_KEEP_RATIO') == '0.7500'

    # Test async decode knob (Patch 0005)
    test_settings['llamacpp']['async_decode'] = True
    assert extra_env('moe-model').get('LLAMA_SERVER_ASYNC_DECODE') == '1'
    test_settings['llamacpp']['async_decode'] = False
    assert extra_env('moe-model').get('LLAMA_SERVER_ASYNC_DECODE') == '0'
