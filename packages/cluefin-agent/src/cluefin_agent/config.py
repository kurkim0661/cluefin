from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PatModelSettings:
    pat: str
    base_url: str
    model: str
    temperature: float = 0.1

    @classmethod
    def from_env(cls) -> "PatModelSettings":
        pat = os.getenv("CLUEFIN_LLM_PAT", "")
        base_url = os.getenv("CLUEFIN_LLM_BASE_URL", "")
        model = os.getenv("CLUEFIN_LLM_MODEL", "")
        missing = [
            name
            for name, value in (
                ("CLUEFIN_LLM_PAT", pat),
                ("CLUEFIN_LLM_BASE_URL", base_url),
                ("CLUEFIN_LLM_MODEL", model),
            )
            if not value
        ]
        if missing:
            raise RuntimeError(f"Missing LLM settings: {', '.join(missing)}")
        return cls(pat=pat, base_url=base_url.rstrip("/"), model=model)

    def create_model(self):
        from langchain_openai import ChatOpenAI

        # ChatOpenAI sends the supplied PAT as an Authorization: Bearer token.
        # No token value is logged or persisted in report snapshots.
        return ChatOpenAI(
            api_key=self.pat,
            base_url=self.base_url,
            model=self.model,
            temperature=self.temperature,
            max_retries=2,
            timeout=90,
        )
