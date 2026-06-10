"""AzureOpenAIAdapter — wraps Azure OpenAI behind IAIProvider.

This is the ONLY file that may import openai directly.
"""
from __future__ import annotations
import asyncio
import json
import logging
import re

from app.application.ports.ai import IAIProvider

logger = logging.getLogger(__name__)


class AzureOpenAIAdapter(IAIProvider):
    """Azure OpenAI adapter. Falls back to standard OpenAI if Azure not configured."""

    def __init__(
        self,
        api_key:    str = "",
        endpoint:   str = "",
        deployment: str = "",
        api_version: str = "2025-01-01-preview",
        std_api_key: str = "",
        std_model:   str = "gpt-4o-mini",
    ) -> None:
        self._azure_key     = api_key
        self._azure_ep      = endpoint
        self._azure_dep     = deployment
        self._azure_ver     = api_version
        self._std_key       = std_api_key
        self._std_model     = std_model
        self._client        = None
        self._use_azure     = bool(api_key and endpoint and deployment)

    def _get_client(self):
        if self._client is not None:
            return self._client
        try:
            if self._use_azure:
                from openai import AzureOpenAI
                self._client = AzureOpenAI(
                    api_key=self._azure_key,
                    azure_endpoint=self._azure_ep,
                    api_version=self._azure_ver,
                )
            elif self._std_key:
                from openai import OpenAI
                self._client = OpenAI(api_key=self._std_key)
        except Exception as exc:
            logger.warning("Could not initialise OpenAI client: %s", exc)
            self._client = None
        return self._client

    async def complete(
        self,
        system_prompt: str,
        user_prompt:   str,
        temperature:   float = 0.1,
        max_tokens:    int   = 500,
    ) -> dict:
        client = self._get_client()
        if not client:
            return {"error": "AI not configured"}

        loop = asyncio.get_running_loop()

        def _call():
            model = self._azure_dep if self._use_azure else self._std_model
            resp = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user",   "content": user_prompt},
                ],
                temperature=temperature,
                max_tokens=max_tokens,
                response_format={"type": "json_object"},
            )
            content = resp.choices[0].message.content or "{}"
            return self._safe_parse(content)

        try:
            return await loop.run_in_executor(None, _call)
        except Exception as exc:
            logger.warning("AI complete() failed: %s", exc)
            return {"error": str(exc)}

    async def batch_complete(
        self,
        requests: list[dict],
        max_concurrency: int = 5,
    ) -> list[dict]:
        """Run up to max_concurrency completions in parallel."""
        semaphore = asyncio.Semaphore(max_concurrency)

        async def _bounded(req: dict) -> dict:
            async with semaphore:
                return await self.complete(
                    system_prompt=req.get("system", ""),
                    user_prompt=req.get("user", ""),
                    temperature=req.get("temperature", 0.1),
                    max_tokens=req.get("max_tokens", 500),
                )

        results = await asyncio.gather(
            *[_bounded(r) for r in requests],
            return_exceptions=True,
        )
        return [
            r if isinstance(r, dict) else {"error": str(r)}
            for r in results
        ]

    async def health_check(self) -> bool:
        client = self._get_client()
        return client is not None

    @staticmethod
    def _safe_parse(content: str) -> dict:
        """Parse JSON, stripping markdown fences if present."""
        content = content.strip()
        # Strip ```json ... ``` wrappers
        content = re.sub(r"^```(?:json)?\s*", "", content)
        content = re.sub(r"\s*```$", "", content)
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            # Try extracting first {...} block
            match = re.search(r"\{.*\}", content, re.DOTALL)
            if match:
                try:
                    return json.loads(match.group())
                except Exception:
                    pass
        return {"raw": content}
