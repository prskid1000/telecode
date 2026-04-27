"""Task registry for telecode."""

from __future__ import annotations

import logging
from typing import Any, Dict

from services.task.task_manager import get_task_queue
from services.task.handlers.claude_code import claude_code_task
from services.task.handlers.gemini import gemini_task

logger = logging.getLogger("telecode.services.task")

def register_default_tasks():
    queue = get_task_queue()

    # 1. Existing ECHO task
    def echo_handler(text: str) -> Dict[str, Any]:
        return {"echo": text}

    queue.register_handler(
        "ECHO",
        echo_handler,
        description="Simple echo task for testing",
        params_schema={
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"]
        }
    )

    # Shared schema: pre-rendered prompt OR structured agent/job fields.
    _agent_task_schema = {
        "type": "object",
        "properties": {
            "prompt": {"type": "string", "description": "Pre-rendered prompt (text or XML). Mutually exclusive with agent+job."},
            "is_local": {"type": "boolean", "description": "Point to the local proxy and currently loaded model", "default": False},
            "agent": {
                "type": "object",
                "description": "Agent struct (id, name, instructions). Server renders <agent_task> XML.",
                "properties": {
                    "id": {"type": "string"},
                    "name": {"type": "string"},
                    "instructions": {"type": "string"},
                },
            },
            "job": {
                "type": "object",
                "description": "Job struct (workspace_id, task_description).",
                "properties": {
                    "workspace_id": {"type": "string"},
                    "task_description": {"type": "string"},
                },
            },
            "agent_files": {"type": "array", "items": {"type": "object"}, "description": "Optional list of {path: ...}"},
            "job_files": {"type": "array", "items": {"type": "object"}, "description": "Optional list of {path: ...}"},
        },
        "oneOf": [
            {"required": ["prompt"]},
            {"required": ["agent", "job"]},
        ],
    }

    # 2. Claude Code Task
    queue.register_handler(
        "CLAUDE_CODE",
        claude_code_task,
        description="Run Claude Code in a stateful session folder",
        params_schema=_agent_task_schema,
    )

    # 3. Gemini Task
    queue.register_handler(
        "GEMINI",
        gemini_task,
        description="Run Gemini CLI in a stateful session folder",
        params_schema=_agent_task_schema,
    )

    logger.info("Task handlers registered (ECHO, CLAUDE_CODE, GEMINI)")
