from fastapi import Request


def get_request_ip(request: Request) -> str | None:
    """
    Get request IP from middleware cache or fallback to extraction.
    """
    meta = getattr(request.state, "meta", None)
    ip = meta.get("ip") if meta else None
    if ip is None:
        ip = get_client_ip(request)
    return ip
