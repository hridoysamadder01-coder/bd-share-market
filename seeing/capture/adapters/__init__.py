"""Source adapters. Each adapter declares the canonical fields it OBSERVES;
everything else is NOT_OBSERVABLE for that source. Fetch returns raw bytes;
parsing happens on replay (and in tests) — never in the capture path."""
