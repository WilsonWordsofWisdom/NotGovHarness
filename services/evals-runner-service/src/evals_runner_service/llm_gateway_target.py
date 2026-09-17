"""The system-under-test adapter (D-077): one `llm-gateway` chat completion per golden, producing
`actual_output`. No real A2A-callable agent exists anywhere in this platform yet — this is the
same `TokenClient` + mutate-before-call pattern Mem0 uses (D-062), not LlamaIndex's cached-client
one (D-069): the underlying `openai.AsyncOpenAI` client is constructed fresh, and its `api_key`
attribute is read fresh by the SDK on every request either way.
"""

from __future__ import annotations

import openai

from platform_core.llm_gateway_auth import TokenClient, call_with_auth_retry


class LlmGatewayTarget:
    def __init__(self, base_url: str, model: str, token_client: TokenClient) -> None:
        self._client = openai.AsyncOpenAI(base_url=base_url, api_key="unset")
        self._model = model
        self._token_client = token_client

    async def complete(self, prompt: str) -> str:
        def apply_token(token: str) -> None:
            self._client.api_key = token

        async def call() -> str:
            resp = await self._client.chat.completions.create(
                model=self._model,
                messages=[{"role": "user", "content": prompt}],
            )
            return resp.choices[0].message.content or ""

        return await call_with_auth_retry(self._token_client, apply_token, call)
