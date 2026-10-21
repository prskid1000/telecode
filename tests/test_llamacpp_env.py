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
