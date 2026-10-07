"""Extraction layers, cheapest and most reliable first. Each returns candidates; none invents a value."""
from dataclasses import dataclass, field
from decimal import Decimal

from product_extractor.schema import Confidence, SourceMethod


@dataclass(frozen=True)
class Candidate:
    value: str | Decimal
    method: SourceMethod
    confidence: Confidence


@dataclass
class LayerResult:
    method: SourceMethod
    fields: dict[str, Candidate] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def put(self, name: str, value: str | Decimal | None, confidence: Confidence) -> None:
        """Record a value unless it is empty or the layer already has one for this field."""
        if value is None or value == "" or name in self.fields:
            return
        self.fields[name] = Candidate(value, self.method, confidence)


_ORDER = ("high", "medium", "low")


def lower(confidence: Confidence) -> Confidence:
    """One step less confident."""
    return _ORDER[min(_ORDER.index(confidence) + 1, 2)]  # type: ignore[return-value]
