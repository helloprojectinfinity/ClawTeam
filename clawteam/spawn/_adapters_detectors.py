"""Command detection helpers — identify runtime type from command list."""

from __future__ import annotations

from pathlib import Path


def command_basename(command: list[str]) -> str:
    """Return the normalized executable basename for a command."""
    if not command:
        return ""
    return Path(command[0]).name.lower()


def is_claude_command(command: list[str]) -> bool:
    """Check if the command is a Claude CLI invocation."""
    return command_basename(command) in ("claude", "claude-code")


def is_codex_command(command: list[str]) -> bool:
    """Check if the command is a Codex CLI invocation."""
    return command_basename(command) in ("codex", "codex-cli")


def _is_codex_noninteractive_command(command: list[str]) -> bool:
    """Return True when Codex is invoked in a non-interactive subcommand mode."""
    if len(command) < 2:
        return False
    return command[1] in {
        "exec",
        "e",
        "review",
        "resume",
        "fork",
        "cloud",
        "mcp",
        "mcp-server",
        "app-server",
        "completion",
        "sandbox",
        "debug",
        "apply",
        "login",
        "logout",
        "features",
    }


def is_nanobot_command(command: list[str]) -> bool:
    """Check if the command is a nanobot CLI invocation."""
    return command_basename(command) == "nanobot"


def is_gemini_command(command: list[str]) -> bool:
    """Check if the command is a Gemini CLI invocation."""
    return command_basename(command) == "gemini"


def is_kimi_command(command: list[str]) -> bool:
    """Check if the command is a Kimi CLI invocation."""
    return command_basename(command) == "kimi"


def is_qwen_command(command: list[str]) -> bool:
    """Check if the command is a Qwen Code CLI invocation."""
    return command_basename(command) in ("qwen", "qwen-code")


def is_opencode_command(command: list[str]) -> bool:
    """Check if the command is an OpenCode CLI invocation."""
    return command_basename(command) == "opencode"


def is_openclaw_command(command: list[str]) -> bool:
    """Check if the command is an OpenClaw CLI invocation."""
    return command_basename(command) == "openclaw"


def is_interactive_cli(command: list[str]) -> bool:
    """Check if the command is a known interactive AI coding CLI."""
    return (
        is_claude_command(command)
        or is_codex_command(command)
        or is_nanobot_command(command)
        or is_gemini_command(command)
        or is_kimi_command(command)
        or is_qwen_command(command)
        or is_opencode_command(command)
        or is_openclaw_command(command)
    )


def command_has_workspace_arg(command: list[str]) -> bool:
    """Return True when a command already specifies a workspace."""
    return "-w" in command or "--workspace" in command
