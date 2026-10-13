"""Машина состояний заказа и общие операции."""
from fastapi import HTTPException

TRANSITIONS = {
    "created": {"open", "sent", "cancelled"},
    "open": {"sent", "awaiting_payment", "cancelled"},
    "sent": {"cooking", "ready", "awaiting_payment", "cancelled"},
    "cooking": {"ready", "awaiting_payment", "cancelled"},
    "ready": {"awaiting_payment", "cancelled"},
    "awaiting_payment": {"paid", "cancelled"},
    "paid": {"closed", "awaiting_payment"},   # awaiting_payment — если возврат сделал заказ неоплаченным
    "closed": set(),
    "cancelled": set(),
}

def check_transition(order, new_status: str):
    allowed = TRANSITIONS.get(order.status, set())
    if new_status not in allowed and new_status != order.status:
        raise HTTPException(409, f"Недопустимый переход статуса заказа: {order.status} → {new_status}")

EDITABLE = {"created", "open"}  # свободное редактирование состава

def is_editable(status: str) -> bool:
    return status in EDITABLE
