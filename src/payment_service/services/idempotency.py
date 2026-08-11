import hashlib
import json

from payment_service.services.commands import NewPayment


def request_fingerprint(new_payment: NewPayment) -> str:
    """
    Отпечаток запроса, по которому видно переиспользование ключа идемпотентности.

    sort_keys обязателен: без него {"a": 1, "b": 2} и {"b": 2, "a": 1} в метаданных
    дадут разные отпечатки, и повтор того же запроса прилетит клиенту как 409.
    """
    canonical = json.dumps(
        new_payment.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()
