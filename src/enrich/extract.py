"""Call the extraction model with a forced tool call and validate the result (SDD §4.3, ADR-005)."""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from enrich.validate import errors, normalise

HERE = Path(__file__).parent
TOOL_NAME = "record_posting_fields"
MAX_DESCRIPTION_CHARS = 12_000


@dataclass
class Extraction:
    refnr: str
    content_hash: str
    fields: dict | None = None
    problems: list[str] = field(default_factory=list)
    attempts: int = 0
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def ok(self) -> bool:
        return self.fields is not None


class Extractor:
    def __init__(self, client: Any, model_id: str, prompt_version: str = "v1") -> None:
        self.client = client
        self.model_id = model_id
        self.prompt_version = prompt_version
        prompt_dir = HERE / "prompts" / prompt_version
        self.system = prompt_dir.joinpath("system.md").read_text()
        self.user_template = prompt_dir.joinpath("user.md").read_text()
        self.tool_config = {
            "tools": [
                {
                    "toolSpec": {
                        "name": TOOL_NAME,
                        "description": "Record the structured fields extracted from the job advertisement.",
                        "inputSchema": {"json": json.loads((HERE / "schema.json").read_text())},
                    }
                }
            ],
            # With a single tool, "any" forces the model to call it (supported across Nova models).
            "toolChoice": {"any": {}},
        }

    def user_message(self, posting: dict) -> str:
        return self.user_template.format(
            title=posting.get("title") or "",
            employer=posting.get("employer") or "",
            location=posting.get("location") or "",
            description=(posting.get("description") or "")[:MAX_DESCRIPTION_CHARS],
        )

    def _converse(self, messages: list) -> dict:
        return self.client.converse(
            modelId=self.model_id,
            system=[{"text": self.system}],
            messages=messages,
            toolConfig=self.tool_config,
            inferenceConfig={"temperature": 0, "maxTokens": 1000},
        )

    def extract(self, posting: dict) -> Extraction:
        """One call, plus one retry that shows the model its validation errors."""
        result = Extraction(refnr=posting["refnr"], content_hash=posting["content_hash"])
        messages: list = [{"role": "user", "content": [{"text": self.user_message(posting)}]}]
        for _ in range(2):
            response = self._converse(messages)
            result.attempts += 1
            usage = response.get("usage", {})
            result.input_tokens += usage.get("inputTokens", 0)
            result.output_tokens += usage.get("outputTokens", 0)

            content = response["output"]["message"]["content"]
            tool_use = next((c["toolUse"] for c in content if "toolUse" in c), None)
            if tool_use is None:
                result.problems = ["the model did not call the tool"]
                messages += [
                    {"role": "assistant", "content": content},
                    {"role": "user", "content": [{"text": f"Call the tool {TOOL_NAME} with the extracted fields."}]},
                ]
                continue

            result.problems = errors(tool_use["input"])
            if not result.problems:
                result.fields = normalise(tool_use["input"])
                return result
            messages += [
                {"role": "assistant", "content": content},
                {
                    "role": "user",
                    "content": [
                        {
                            "toolResult": {
                                "toolUseId": tool_use["toolUseId"],
                                "status": "error",
                                "content": [
                                    {
                                        "text": "Fix these problems and call the tool again: "
                                        + "; ".join(result.problems)
                                    }
                                ],
                            }
                        }
                    ],
                },
            ]
        return result
