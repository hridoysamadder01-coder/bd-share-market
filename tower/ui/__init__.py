"""Observation Tower UI package.

The UI bridge is presentation/read-only: it attaches routes that expose already
captured public/live artefacts.  It never changes engine or research behaviour.
"""


def _install_connected_layers() -> None:
    from . import market_api
    from .connected_layers import attach_connected_layers

    if getattr(market_api.attach_market_api, "_connected_layers", False):
        return
    original = market_api.attach_market_api

    def combined(app) -> None:
        original(app)
        attach_connected_layers(app)

    combined._connected_layers = True  # type: ignore[attr-defined]
    market_api.attach_market_api = combined


_install_connected_layers()
