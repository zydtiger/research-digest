"""Call local OpenAI-compatible models with validated structured output."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any

import httpx
import openai
from pydantic import BaseModel, ValidationError

from .config import ModelConfig

AVAILABILITY_TIMEOUT_SECONDS = 10.0
MAX_ATTEMPTS = 2
REQUEST_TIMEOUT_SECONDS = 1800.0


class ModelUnavailable(Exception):
    """A local model could not be reached."""


def _http_client() -> httpx.Client:
    # Model endpoints are reached directly, never through a configured proxy.
    return httpx.Client(trust_env=False, timeout=REQUEST_TIMEOUT_SECONDS)


def check_available(model: ModelConfig) -> None:
    """Raise ModelUnavailable unless the endpoint is reachable and serves the model."""
    url = f"{model.base_url.rstrip('/')}/models"
    try:
        with httpx.Client(trust_env=False, timeout=AVAILABILITY_TIMEOUT_SECONDS) as http:
            response = http.get(url)
        response.raise_for_status()
        served = [entry["id"] for entry in response.json()["data"]]
    except httpx.TransportError as error:
        raise ModelUnavailable(f"host not reachable: {url}: {error}") from error
    except (httpx.HTTPStatusError, KeyError, TypeError, ValueError) as error:
        raise ModelUnavailable(f"endpoint not available: {url}: {error}") from error
    if model.model not in served:
        raise ModelUnavailable(f"model {model.model} not served by {url}; serving: {served}")


def complete[T: BaseModel](
    client: openai.OpenAI,
    model: ModelConfig,
    system: str,
    user: str,
    schema: type[T],
    max_tokens: int,
) -> T:
    """Request one schema-constrained reply, retrying once on an invalid reply."""
    response_format: Any = {
        "type": "json_schema",
        "json_schema": {"name": schema.__name__, "schema": schema.model_json_schema()},
    }
    last_error: Exception | None = None
    for _ in range(MAX_ATTEMPTS):
        try:
            response = client.chat.completions.create(
                model=model.model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                max_tokens=max_tokens,
                temperature=0,
                response_format=response_format,
                extra_body={"chat_template_kwargs": {"enable_thinking": False}},
            )
            return schema.model_validate_json(response.choices[0].message.content or "")
        except (ValidationError, openai.BadRequestError) as error:
            last_error = error
    raise ValueError(f"no valid {schema.__name__} reply: {last_error}")


def make_client(model: ModelConfig) -> openai.OpenAI:
    # The SDK requires a key, and these local servers do not check it.
    return openai.OpenAI(base_url=model.base_url, api_key="local", http_client=_http_client())


def run_all[I, O](
    function: Callable[[I], O],
    inputs: Iterable[I],
    max_concurrency: int,
    progress_label: str,
) -> list[tuple[I, O | None, Exception | None]]:
    """Apply a function concurrently, returning each result or the error for that input."""
    results: list[tuple[I, O | None, Exception | None]] = []
    connection_errors = (openai.APIConnectionError, openai.InternalServerError)
    with ThreadPoolExecutor(max_workers=max_concurrency) as pool:
        futures: list[tuple[I, Future[O]]] = [
            (value, pool.submit(function, value)) for value in inputs
        ]
        for index, (value, future) in enumerate(futures, start=1):
            try:
                results.append((value, future.result(), None))
            except connection_errors as error:
                for _, pending in futures:
                    pending.cancel()
                raise ModelUnavailable(f"model request failed: {error}") from error
            except (ValueError, openai.APIError) as error:
                results.append((value, None, error))
            if index % 100 == 0 or index == len(futures):
                print(f"{progress_label}: {index}/{len(futures)}", flush=True)
    return results
