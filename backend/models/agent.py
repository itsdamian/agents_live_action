"""Agent configuration data model.

Parsed from per-project agent YAML files
(agents/PM_agent.yaml, agents/SWE_agent.yaml, ...).
"""

from __future__ import annotations

from pydantic import BaseModel


class AgentConfig(BaseModel):
    """Configuration for a single agent, loaded from a YAML file.

    Supports all YAML fields: agent name, role, description, the LLM
    provider/model to use, the environment variable holding the API key,
    a retry ceiling, and the system prompt.
    """

    name: str
    role: str
    description: str
    llm_provider: str  # openai | anthropic | gemini | mock ("mock" forces mock LLM)
    llm_model: str
    api_key_env: str  # name of the env var holding the API key
    max_retries: int = 3
    system_prompt: str
