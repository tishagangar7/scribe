"""Real Claude API-backed LLMClient for agent/graph.py.

Swaps in for StubLLMClient once ANTHROPIC_API_KEY is configured (in .env).
Every call is routed through `ctx.llm_step` upstream (see plan_node/
code_node/critic_node in agent/graph.py), so real spend is tracked against
the run's token/cost budget exactly like the stub's $0 calls are -- this
client just reports real usage numbers instead of zeros.
"""

from __future__ import annotations

import asyncio

import anthropic
from dotenv import load_dotenv

load_dotenv()

DEFAULT_MODEL = "claude-sonnet-5"
DEFAULT_MAX_TOKENS = 4096

# $ per 1M tokens (input, output). Sonnet 5's intro rate applies through
# 2026-08-31; update if pricing changes. A model missing from this table
# costs $0 here -- token tracking still works, cost tracking silently
# doesn't, which is why every model this client defaults to is listed.
_PRICING_PER_MILLION: dict[str, tuple[float, float]] = {
    "claude-sonnet-5": (2.00, 10.00),
    "claude-opus-4-8": (5.00, 25.00),
    "claude-haiku-4-5": (1.00, 5.00),
}


def _cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    input_rate, output_rate = _PRICING_PER_MILLION.get(model, (0.0, 0.0))
    return (input_tokens * input_rate + output_tokens * output_rate) / 1_000_000


class AnthropicLLMClient:
    """Real LLMClient backed by the Claude API.

    `complete`/`complete_many` return `(result, tokens, cost_usd)` -- the
    shape `ctx.llm_step` expects -- so real spend flows into the run's
    budget accounting exactly the way the stub's $0 calls do.
    """

    def __init__(
        self, model: str = DEFAULT_MODEL, max_tokens: int = DEFAULT_MAX_TOKENS
    ) -> None:
        self._client = anthropic.AsyncAnthropic()
        self._model = model
        self._max_tokens = max_tokens

    async def _call(self, prompt: str) -> tuple[str, int, float]:
        response = await self._client.messages.create(
            model=self._model,
            max_tokens=self._max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(block.text for block in response.content if block.type == "text")
        tokens = response.usage.input_tokens + response.usage.output_tokens
        cost = _cost_usd(
            self._model, response.usage.input_tokens, response.usage.output_tokens
        )
        return text, tokens, cost

    async def complete(self, node: str, prompt: str) -> tuple[str, int, float]:
        return await self._call(prompt)

    async def complete_many(
        self, node: str, prompt: str, n: int
    ) -> tuple[list[str], int, float]:
        # Nudge each candidate toward a distinct approach -- identical
        # prompts to the same model otherwise tend to converge on very
        # similar completions, defeating the point of racing N candidates.
        results = await asyncio.gather(
            *(
                self._call(
                    f"{prompt}\n\n(This is candidate {i + 1} of {n} -- try a "
                    f"distinct approach from the others.)"
                )
                for i in range(n)
            )
        )
        texts = [r[0] for r in results]
        total_tokens = sum(r[1] for r in results)
        total_cost = sum(r[2] for r in results)
        return texts, total_tokens, total_cost
