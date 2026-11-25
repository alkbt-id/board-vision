"""Claude for the explanations of tasks (vision/sources/prepare.py), through the user's own Claude Code login (the
Claude Agent SDK, as «Шахматный тренер»'s chat — no API key).

One stateless request per task: no tools, no settings or CLAUDE.md files of the user (setting_sources=[]), the
answer as JSON by a schema (structured output). The model and effort come from config.toml (claude_model,
claude_effort), default Opus 5.5 at high effort: the explanation must be right about the position.
"""

import asyncio

from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, query

from . import config

MODEL, EFFORT = "claude-opus-5-5", "high"
TIMEOUT = 300  # seconds for one task


class ClaudeError(RuntimeError):
    """Claude did not answer: not signed in, a usage limit, no network."""


async def _ask(system: str, prompt: str, schema: dict) -> dict:
    options = ClaudeAgentOptions(
        model=str(config.get("claude_model", MODEL)),
        effort=str(config.get("claude_effort", EFFORT)),  # type: ignore[arg-type]
        system_prompt=system,
        tools=[],
        setting_sources=[],
        max_turns=4,
        cwd=str(config.HOME),
        output_format={"type": "json_schema", "schema": schema},
    )
    result: ResultMessage | None = None
    async for msg in query(prompt=prompt, options=options):
        if isinstance(msg, ResultMessage):
            result = msg
    if result is None or result.is_error:
        raise ClaudeError((result.result if result else "") or (result.subtype if result else "no answer"))
    out = result.structured_output
    if not isinstance(out, dict):
        raise ClaudeError("answer is not JSON")
    return out


def ask(system: str, prompt: str, schema: dict) -> dict:
    """The JSON answer to one request; ClaudeError when Claude does not answer."""
    try:
        return asyncio.run(asyncio.wait_for(_ask(system, prompt, schema), TIMEOUT))
    except ClaudeError:
        raise
    except Exception as e:  # noqa: BLE001 — the SDK's own errors (CLI not found, process error, timeout)
        raise ClaudeError(f"{type(e).__name__}: {e}") from e
