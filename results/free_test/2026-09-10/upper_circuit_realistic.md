# Upper-circuit continuation — realistic entry (2026-09-10)

Entry: buy at day-t+1 open (cannot fill at day-t close on locked stock)
Exit:  day-t+h close
Cost: 1% round trip

Signal→next open gap: mean +0.0325, median +0.0267
P(gap>0): 0.908

| h | sig | ctrl | diff | t | net(1%cost) | P(net>0) |
|---|---|---|---|---|---|---|
| 1 | -0.0044 | -0.0078 | +0.0034 | +4.83 | -0.0144 | 0.362 |
| 3 | +0.0027 | -0.0061 | +0.0088 | +4.62 | -0.0073 | 0.421 |
| 5 | +0.0054 | -0.0064 | +0.0118 | +5.27 | -0.0046 | 0.407 |
| 10 | +0.0044 | -0.0063 | +0.0106 | +4.46 | -0.0056 | 0.361 |
