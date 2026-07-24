"""Build provenance, baked into the Docker image by CI and exposed at /api/version."""

from __future__ import annotations

import os


def get_version() -> dict[str, str]:
    return {
        "commit": os.environ.get("APP_COMMIT", "dev"),
        "built_at": os.environ.get("APP_BUILD_TIME", ""),
    }
