"""Structured error logging service for IRA tools and autonomous execution."""

from __future__ import annotations

import datetime
import json
import logging
import os
import traceback
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_ERROR_LOG_DIR = Path("logs/errors")


def get_error_log_dir() -> Path:
    """Return the resolved error log directory, ensuring it exists."""
    configured = os.getenv("IRA_ERROR_LOG_DIR", "").strip()
    target_dir = Path(configured) if configured else DEFAULT_ERROR_LOG_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    return target_dir


def log_tool_error(
    tool_name: str,
    error: Exception,
    args: dict[str, Any] | None = None,
    task_context: str | None = None,
    recovery_advice: str | None = None,
) -> Path:
    """Write a structured diagnostic error log to the error directory.

    Args:
        tool_name: The name of the tool that failed.
        error: The caught exception.
        args: Optional dictionary of arguments provided to the tool.
        task_context: Optional human request / task description.
        recovery_advice: Optional suggested fallback action.

    Returns:
        Path to the written error log file.
    """
    log_dir = get_error_log_dir()
    now = datetime.datetime.now(datetime.timezone.utc)
    timestamp_str = now.strftime("%Y%m%d_%H%M%S")
    clean_tool = "".join(
        c if c.isalnum() or c in ("-", "_") else "_" for c in tool_name
    )
    log_filename = f"error_{timestamp_str}_{clean_tool}_{os.getpid()}.log"
    log_path = log_dir / log_filename

    err_type = error.__class__.__name__
    err_msg = str(error) or err_type
    tb_str = traceback.format_exc()
    if tb_str.strip() == "NoneType: None":
        tb_str = f"Exception: {err_type}: {err_msg}"

    fallback = recovery_advice or (
        "If interacting with a browser element failed, call capture_screen to inspect "
        "the visual screen and complete the action using click_desktop_element or click_mouse."
    )

    clean_args = dict(args) if args else {}
    # Ensure serializable args
    try:
        args_repr = json.dumps(clean_args, default=str, indent=2)
    except Exception:
        args_repr = repr(clean_args)

    content = f"""================================================================================
IRA TOOL EXECUTION ERROR LOG
================================================================================
Timestamp:    {now.isoformat()}
Tool:         {tool_name}
Task Context: {task_context or "General execution"}
Error Type:   {err_type}
Error Detail: {err_msg}

Arguments:
{args_repr}

Traceback:
{tb_str}

Recommended Fallback:
{fallback}
================================================================================
"""

    try:
        log_path.write_text(content, encoding="utf-8")
        logger.info("Recorded error log to %s for tool %s", log_path, tool_name)
    except Exception as io_err:
        logger.error("Failed to write tool error log to %s: %s", log_path, io_err)

    return log_path
