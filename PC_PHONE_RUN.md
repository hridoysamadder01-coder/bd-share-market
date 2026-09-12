# PC + phone runtime

Run from the repository root:

```bash
python run_market_ui.py
```

The same process serves:

- PC: `http://127.0.0.1:8765`
- phone on the same Wi-Fi/LAN: use one of the `PHONE:` URLs printed by the launcher
- runtime check: `/api/runtime/info`
- on-disk data inventory: `/api/data/inventory`

If the phone cannot connect while the PC URL works, the blocker is outside the app process (most commonly host firewall or LAN isolation). Do not change engine/research code to work around that network boundary.

The launcher does not expose raw file contents. Data presence is visible as metadata only; connected market layers continue to use the existing read-only endpoints.
