# Upper-circuit continuation — adversarial round

Panel: 2012-10-01→2026-01-22 (sealed holdout dropped)
raw signals: 3,279   episodes (10-day gap): 2,207   inflation: 1.49x

## A. Episode-level matched controls

| h | sig | ctrl | diff | t |
|---|---|---|---|---|
| 1 | +0.0255 | +0.0001 | +0.0254 | +22.33 |
| 3 | +0.0296 | +0.0017 | +0.0278 | +12.84 |
| 5 | +0.0313 | +0.0020 | +0.0293 | +10.51 |
| 10 | +0.0296 | +0.0013 | +0.0283 | +7.26 |

## B. MFE / MAE (5 sessions)

- signal: MFE +0.1261 / MAE -0.0461, med MFE +0.0955 / MAE -0.0414
- control: MFE +0.0726 / MAE -0.0506

## C. After cost (1% round trip)

- h=1: sig net +0.0155, ctrl net -0.0099, P(net>0)=0.590
- h=3: sig net +0.0196, ctrl net -0.0083, P(net>0)=0.528
- h=5: sig net +0.0213, ctrl net -0.0080, P(net>0)=0.492
- h=10: sig net +0.0196, ctrl net -0.0087, P(net>0)=0.437
