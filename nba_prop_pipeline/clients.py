from __future__ import annotations

import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, Optional

import requests

from .config import PipelineConfig

logger = logging.getLogger(__name__)


class CachedHTTPClient:
    """HTTP client with simple disk caching and resilient retries."""

    def __init__(self, config: PipelineConfig, *, force_refresh: bool = False):
        self.config = config
        self.force_refresh = force_refresh
        self.session = requests.Session()
        self.session.headers.update(config.request_headers)
        config.cache_dir.mkdir(parents=True, exist_ok=True)

    def _cache_path(self, namespace: str, url: str, params: Optional[Dict[str, Any]]) -> Path:
        payload = json.dumps({"url": url, "params": params or {}}, sort_keys=True).encode("utf-8")
        digest = hashlib.sha256(payload).hexdigest()[:16]
        return self.config.cache_dir / namespace / f"{digest}.json"

    def get_json(
        self,
        url: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        namespace: str,
        force_refresh: Optional[bool] = None,
    ) -> Dict[str, Any]:
        use_force_refresh = self.force_refresh if force_refresh is None else force_refresh
        cache_path = self._cache_path(namespace, url, params)
        cache_path.parent.mkdir(parents=True, exist_ok=True)

        if cache_path.exists() and not use_force_refresh:
            return json.loads(cache_path.read_text())

        last_error: Optional[Exception] = None
        for attempt in range(1, 6):
            try:
                timeout = self.config.timeout_seconds
                if attempt == 3:
                    timeout = 80
                elif attempt == 4:
                    timeout = 100
                elif attempt == 5:
                    timeout = 120
                response = self.session.get(url, params=params, timeout=timeout)
                response.raise_for_status()
                data = response.json()
                cache_path.write_text(json.dumps(data))
                return data
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                logger.warning("Request attempt %s failed for %s: %s", attempt, url, exc)
                if attempt < 5:
                    delay_seconds = 2 ** attempt
                    logger.info("Backing off for %s seconds before retrying %s", delay_seconds, url)
                    time.sleep(delay_seconds)

        if cache_path.exists():
            logger.warning("Using stale cache after failures for %s", url)
            return json.loads(cache_path.read_text())

        raise RuntimeError(f"Failed to fetch {url}: {last_error}")
