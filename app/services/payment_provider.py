"""Simulated payment provider. No real money moves and no external service is called."""

import random
import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from app.core.config import PaymentSimulationMode
from app.models.payment import PaymentStatus


@dataclass(frozen=True)
class ChargeResult:
    provider_payment_id: str
    status: PaymentStatus


class PaymentProvider(Protocol):
    name: str

    def charge(self, amount: Decimal, reference: str) -> ChargeResult: ...

    def refund(self, provider_payment_id: str, amount: Decimal) -> None:
        """Return a captured payment. Raises if the provider rejects the refund."""
        ...

    def get_status(self, provider_payment_id: str) -> PaymentStatus:
        """Ask the provider for a charge's current status (used to reconcile stuck payments)."""
        ...


class SimulatedPaymentProvider:
    name = "simulated"

    def __init__(
        self,
        mode: PaymentSimulationMode,
        success_rate: float = 1.0,
        rng: random.Random | None = None,
    ) -> None:
        self._mode = mode
        self._success_rate = success_rate
        self._rng = rng or random.Random()  # noqa: S311 - simulation only, not security-sensitive

    def charge(self, amount: Decimal, reference: str) -> ChargeResult:
        return ChargeResult(provider_payment_id=f"pay_{uuid.uuid4().hex}", status=self._decide_outcome())

    def refund(self, provider_payment_id: str, amount: Decimal) -> None:
        """Simulated refunds always succeed, whatever the charge simulation mode."""

    def get_status(self, provider_payment_id: str) -> PaymentStatus:
        """The simulated provider keeps no state: a pending charge "settles" according to the
        current simulation mode, and in ``pending`` mode it is still unresolved."""
        return self._decide_outcome()

    def _decide_outcome(self) -> PaymentStatus:
        match self._mode:
            case PaymentSimulationMode.SUCCESS:
                return PaymentStatus.SUCCESS
            case PaymentSimulationMode.FAILURE:
                return PaymentStatus.FAILED
            case PaymentSimulationMode.PENDING:
                return PaymentStatus.PENDING
            case PaymentSimulationMode.RANDOM:
                return PaymentStatus.SUCCESS if self._rng.random() < self._success_rate else PaymentStatus.FAILED
