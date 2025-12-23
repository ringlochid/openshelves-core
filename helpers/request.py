from fastapi import Request


def get_request_ip(request: Request) -> str | None:
    """
    Get request IP from middleware cache or fallback to extraction.
    Handles X-Forwarded-For for AWS ALB/proxy deployments.
    """
    meta = getattr(request.state, "meta", None)
    ip = meta.get("ip") if meta else None
    if ip is None:
        # Check proxy headers for AWS/nginx
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            ip = forwarded.split(",")[0].strip()
        else:
            ip = request.client.host if request.client else None
    return ip
