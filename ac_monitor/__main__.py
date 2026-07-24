"""Run the AC Monitor service: ``python -m ac_monitor``.

Loads config from ``$CONFIG_FILE`` (default ``config/config.yaml``); on first
run with no config it seeds one from the built-in defaults so the control panel
has something to persist edits into. Then serves the FastAPI app (dashboard +
API) with the background poller.

This module's existence unguards the CI image build (see
.github/workflows/docker-publish.yml).
"""

from __future__ import annotations

import os

import uvicorn

from . import config as configmod
from .state import AppState
from .web.app import create_app


def _load_or_seed(path: str) -> tuple[configmod.Config, str | None]:
    try:
        return configmod.load(path), path
    except configmod.ConfigError:
        cfg = configmod.from_dict({})  # as-wired defaults
        try:
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            configmod.save(cfg, path)
            return cfg, path
        except OSError:
            return cfg, None  # read-only location: run in-memory


def main() -> None:
    path = os.environ.get("CONFIG_FILE", "config/config.yaml")
    cfg, save_path = _load_or_seed(path)
    app = create_app(AppState(config=cfg, config_path=save_path))
    uvicorn.run(app, host=cfg.web.host, port=cfg.web.port)


if __name__ == "__main__":
    main()
