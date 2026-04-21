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

    # 2. Claude Code Task (Identical to pythonmagic)
    queue.register_handler(
        "CLAUDE_CODE",
        claude_code_task,
        description="Run Claude Code in a stateful session folder",
        params_schema={
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "The prompt to send to Claude Code"},
                "is_local": {"type": "boolean", "description": "Point to the local proxy and currently loaded model", "default": False}
            },
            "required": ["prompt"]
        }
    )

    # 3. Gemini Task
    queue.register_handler(
        "GEMINI",
        gemini_task,
        description="Run Gemini CLI in a stateful session folder",
        params_schema={
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "The prompt to send to Gemini"},
                "is_local": {"type": "boolean", "description": "Point to the local proxy and currently loaded model", "default": False}
            },
            "required": ["prompt"]
        }
    )

    logger.info("Task handlers registered (ECHO, CLAUDE_CODE, GEMINI)")
