"""Small client for the FastAPI backend. The UI never imports app/ directly."""

import os

import requests

API_URL = os.getenv("API_URL", "http://127.0.0.1:8000").rstrip("/")
LLM_TIMEOUT = 1800  # crew mode with a cold rules cache can take several minutes


class ApiError(Exception):
    pass


def _request(method: str, path: str, timeout: float = 30, **kwargs):
    try:
        resp = requests.request(method, f"{API_URL}{path}", timeout=timeout, **kwargs)
    except requests.ConnectionError:
        raise ApiError(f"Can't reach the API at {API_URL}. Start it with: uvicorn app.main:app --reload")
    except requests.Timeout:
        raise ApiError(f"The API took longer than {timeout}s to answer.")
    if resp.status_code >= 400:
        try:
            detail = resp.json().get("detail", resp.text)
        except ValueError:
            detail = resp.text
        raise ApiError(f"{resp.status_code}: {detail}")
    return resp.json()


def health():
    return _request("GET", "/health", timeout=5)


def cards():
    return _request("GET", "/cards", timeout=10)


def recommend(spend_description: str, mode: str, refresh_rules: bool = False):
    body = {"spend_description": spend_description, "mode": mode, "refresh_rules": refresh_rules}
    return _request("POST", "/recommend", timeout=LLM_TIMEOUT, json=body)


def ask(question: str, mode: str):
    return _request("POST", "/ask", timeout=LLM_TIMEOUT, json={"question": question, "mode": mode})


def calculate(profile: dict):
    return _request("POST", "/calculate", timeout=30, json=profile)


def get_rules(card_id: str):
    return _request("GET", f"/rules/{card_id}", timeout=10)


def refresh_rules(card_id: str):
    return _request("POST", f"/rules/{card_id}/refresh", timeout=LLM_TIMEOUT)
