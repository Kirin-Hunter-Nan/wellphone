"""Shared HTTP request validation and error responses."""

from fastapi import Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError


def validation_message(error: ValidationError) -> str:
    first = error.errors(include_url=False)[0]
    return str(first.get("msg", "Request body is invalid"))


def error_response(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"error": {"code": code, "message": message}},
    )


async def read_body(request: Request, max_request_bytes: int) -> bytes | JSONResponse:
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            declared_length = int(content_length)
        except ValueError:
            return error_response(400, "invalid_request", "Content-Length is invalid")
        if declared_length > max_request_bytes:
            return error_response(413, "request_too_large", "Request body is too large")

    body = await request.body()
    if len(body) > max_request_bytes:
        return error_response(413, "request_too_large", "Request body is too large")
    return body
