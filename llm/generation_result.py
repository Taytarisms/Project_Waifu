from dataclasses import asdict, dataclass, field
from typing import Any, Mapping


@dataclass
class GenerationResult:
    text: str = ""
    reasoning: str = ""
    reasoning_kind: str = "none"
    provider: str = ""
    model: str = ""
    usage: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.text = str(self.text or "").strip()
        self.reasoning = str(self.reasoning or "").strip()
        self.reasoning_kind = str(self.reasoning_kind or "none")
        self.provider = str(self.provider or "")
        self.model = str(self.model or "")
        if not isinstance(self.usage, dict):
            self.usage = {}

    def __bool__(self) -> bool:
        return bool(self.text)

    def __str__(self) -> str:
        return self.text

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_value(
        cls,
        value: Any,
        *,
        provider: str = "",
        model: str = "",
    ) -> "GenerationResult":
        if isinstance(value, cls):
            if provider and not value.provider:
                value.provider = provider
            if model and not value.model:
                value.model = model
            return value
        if isinstance(value, Mapping):
            return cls(
                text=value.get("text", value.get("result", "")),
                reasoning=value.get("reasoning", ""),
                reasoning_kind=value.get("reasoning_kind", "none"),
                provider=value.get("provider", provider),
                model=value.get("model", model),
                usage=dict(value.get("usage") or {}),
            )
        return cls(text=value, provider=provider, model=model)


def result_text(value: Any) -> str:
    return GenerationResult.from_value(value).text
