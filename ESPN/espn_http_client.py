"""
ESPN HTTP Client
Handles all ESPN API interactions with robust retry logic and error handling.
Optionally uses CBBpy library for improved resilience.
"""

import os
import time
from typing import Dict, Any, Optional

import requests

from espn_config import (
    ESPN_SUMMARY_URL,
    ESPN_SCOREBOARD_URL,
    DEFAULT_HEADERS,
    REQUEST_TIMEOUT,
    MAX_RETRIES,
    RETRY_INITIAL_DELAY,
    RETRY_BACKOFF,
    ENABLE_CBBPY,
    CBBPY_FALLBACK_TO_ESPN,
)


def fetch_with_retry(
    url: str,
    *,
    headers: Optional[Dict[str, str]] = None,
    timeout: int = REQUEST_TIMEOUT,
    max_retries: int = MAX_RETRIES,
    initial_delay: float = RETRY_INITIAL_DELAY,
    backoff: float = RETRY_BACKOFF,
) -> Dict[str, Any]:
    """
    Fetch JSON from URL with exponential backoff retry logic.
    
    Handles:
    - 429 rate limit errors (respects Retry-After header)
    - 5xx server errors (retries)
    - Timeouts (retries)
    - Other HTTP errors (fails immediately)
    
    Args:
        url: Full URL to fetch
        headers: Optional HTTP headers (uses DEFAULT_HEADERS if None)
        timeout: Request timeout in seconds
        max_retries: Maximum number of retry attempts
        initial_delay: Initial delay between retries (seconds)
        backoff: Backoff multiplier for subsequent retries
        
    Returns:
        Parsed JSON response as dictionary
        
    Raises:
        RuntimeError: After max retries exhausted
        requests.exceptions.HTTPError: For 4xx errors (except 429)
    """
    last_exc: Optional[Exception] = None
    hdrs = (headers or DEFAULT_HEADERS).copy()

    for attempt in range(max_retries):
        if attempt > 0:
            delay = initial_delay * (backoff ** (attempt - 1))
            time.sleep(delay)

        try:
            r = requests.get(url, headers=hdrs, timeout=timeout)

            # Handle rate limiting
            if r.status_code == 429:
                retry_after = r.headers.get("Retry-After")
                extra = float(retry_after) if retry_after and retry_after.isdigit() else (initial_delay * (backoff ** attempt))
                time.sleep(extra)
                last_exc = requests.exceptions.HTTPError(f"429 Too Many Requests: {url}")
                continue

            r.raise_for_status()
            return r.json()

        except requests.exceptions.HTTPError as e:
            last_exc = e
            status = getattr(e.response, "status_code", None)
            # Retry on 5xx errors, fail immediately on other HTTP errors
            if status is not None and status >= 500:
                continue
            raise

        except requests.exceptions.Timeout as e:
            last_exc = e
            continue

        except requests.exceptions.RequestException as e:
            last_exc = e
            continue

    raise RuntimeError(f"Failed after {max_retries} attempts: {url} | last_error={last_exc}")


def fetch_scoreboard(date_yyyymmdd: str, timeout: int = REQUEST_TIMEOUT) -> Dict[str, Any]:
    """
    Fetch scoreboard data for a specific date.
    Uses CBBpy library if enabled, otherwise direct ESPN API.
    
    Args:
        date_yyyymmdd: Date in YYYYMMDD format (e.g., "20240115")
        timeout: Request timeout in seconds
        
    Returns:
        Raw ESPN scoreboard JSON response
        
    Raises:
        RuntimeError: If fetch fails after retries
    """
    # Try CBBpy first if enabled
    if ENABLE_CBBPY:
        try:
            from cbbpy_client import fetch_scoreboard_with_cbbpy_fallback
            return fetch_scoreboard_with_cbbpy_fallback(date_yyyymmdd, timeout)
        except ImportError:
            pass  # CBBpy not available, fall through to direct API
    
    # Direct ESPN API
    url = ESPN_SCOREBOARD_URL.format(date=date_yyyymmdd)
    return fetch_with_retry(url, headers=DEFAULT_HEADERS, timeout=timeout)


def fetch_summary(event_id: str, timeout: int = REQUEST_TIMEOUT) -> Dict[str, Any]:
    """
    Fetch game summary/boxscore data for a specific event.
    Uses CBBpy library if enabled, otherwise direct ESPN API.
    
    Args:
        event_id: ESPN event ID
        timeout: Request timeout in seconds
        
    Returns:
        Raw ESPN summary JSON response
        
    Raises:
        RuntimeError: If fetch fails after retries
    """
    # Try CBBpy first if enabled
    if ENABLE_CBBPY:
        try:
            from cbbpy_client import fetch_summary_with_cbbpy_fallback
            return fetch_summary_with_cbbpy_fallback(event_id, timeout)
        except ImportError:
            pass  # CBBpy not available, fall through to direct API
    
    # Direct ESPN API
    url = ESPN_SUMMARY_URL.format(event_id=event_id)
    return fetch_with_retry(url, headers=DEFAULT_HEADERS, timeout=timeout)
