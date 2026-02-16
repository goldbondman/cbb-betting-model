"""
NCAA Casablanca HTTP Client
Handles all NCAA Casablanca API interactions with robust retry logic and error handling.
"""

import time
from typing import Dict, Any, Optional
from datetime import datetime

import requests

from ncaa_casablanca_config import (
    NCAA_SCOREBOARD_URL,
    NCAA_BOXSCORE_URL,
    DEFAULT_HEADERS,
    REQUEST_TIMEOUT,
    MAX_RETRIES,
    RETRY_INITIAL_DELAY,
    RETRY_BACKOFF,
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


def fetch_scoreboard(year: int, month: int, day: int, timeout: int = REQUEST_TIMEOUT) -> Dict[str, Any]:
    """
    Fetch scoreboard data for a specific date from NCAA Casablanca API.
    
    Args:
        year: Year (e.g., 2024)
        month: Month (1-12)
        day: Day (1-31)
        timeout: Request timeout in seconds
        
    Returns:
        Raw NCAA Casablanca scoreboard JSON response
        
    Raises:
        RuntimeError: If fetch fails after retries
    """
    url = NCAA_SCOREBOARD_URL.format(
        year=year,
        month=str(month).zfill(2),
        day=str(day).zfill(2)
    )
    return fetch_with_retry(url, headers=DEFAULT_HEADERS, timeout=timeout)


def fetch_scoreboard_by_date(date_str: str, timeout: int = REQUEST_TIMEOUT) -> Dict[str, Any]:
    """
    Fetch scoreboard data for a specific date string.
    
    Args:
        date_str: Date in YYYY-MM-DD format
        timeout: Request timeout in seconds
        
    Returns:
        Raw NCAA Casablanca scoreboard JSON response
        
    Raises:
        ValueError: If date_str is not in correct format
        RuntimeError: If fetch fails after retries
    """
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError as e:
        raise ValueError(f"Date must be in YYYY-MM-DD format, got: {date_str}") from e
    
    return fetch_scoreboard(dt.year, dt.month, dt.day, timeout)


def fetch_boxscore(game_id: str, timeout: int = REQUEST_TIMEOUT) -> Dict[str, Any]:
    """
    Fetch game box score data for a specific game from NCAA Casablanca API.
    
    Args:
        game_id: NCAA game ID
        timeout: Request timeout in seconds
        
    Returns:
        Raw NCAA Casablanca box score JSON response
        
    Raises:
        RuntimeError: If fetch fails after retries
    """
    url = NCAA_BOXSCORE_URL.format(game_id=game_id)
    return fetch_with_retry(url, headers=DEFAULT_HEADERS, timeout=timeout)
