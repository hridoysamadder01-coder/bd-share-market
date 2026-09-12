"""Observation Tower UI package.

The UI bridge is presentation/read-only: it attaches routes that expose already
captured public/live artefacts. It never changes engine or research behaviour.
"""


def _install_integrated_layers() -> None:
    from . import market_api
    from .connected_layers import attach_connected_layers
    from .stock_history import attach_stock_history, _find_bars_annotated_parquet, _find_eod_raw_parquet
    from .data_inventory import attach_data_inventory

    if getattr(market_api.attach_market_api, "_integrated_layers", False):
        return
    original = market_api.attach_market_api

    def combined(app) -> None:
        original(app)
        attach_connected_layers(app)
        attach_stock_history(app)
        attach_data_inventory(app)

    combined._integrated_layers = True  # type: ignore[attr-defined]
    market_api.attach_market_api = combined

    # Compatibility for the PR #17 history tests/tools that discovered the two
    # daily files through market_api. The implementation itself lives in
    # stock_history.py so PR #16's market_api truth fixes stay untouched.
    market_api._find_bars_annotated_parquet = _find_bars_annotated_parquet  # type: ignore[attr-defined]
    market_api._find_eod_raw_parquet = _find_eod_raw_parquet  # type: ignore[attr-defined]


_install_integrated_layers()
