/* ============================================================================
   labels.js — PRESENTATION MAPPING LAYER ONLY.
   No engine logic. No API calls. No data transformation beyond naming and
   plain-language phrasing. Everything here exists so a human never has to
   decode an internal id, a feature name, or a z-score.

   PROVENANCE of the sector map (derived, not guessed):
     joined  evidence/public_engine/2026-09-08-full/extract/instruments.csv
             (StockNow, column `sector_id`)
       with  evidence/public_engine/2026-09-08-full/extract/fundamentals.csv
             (BullBD, column `sector`)
       on    symbol  ->  406 symbols matched, 0 conflicts,
                         every sector_id resolved to exactly one name.
     cross-checked against
             evidence/public/2026-09-06/normalized/
             dse_sector_wise_company_list.parquet
             (dsebd.org/by_industrylisting.php, truth=OBSERVED)
     StockNow's numbering == the DSE alphabetical industry list with the
     G-SEC (T.Bond) row removed, plus three StockNow-only buckets (22, 23, 25).
   ========================================================================= */

/* sector_id -> human name.  OBSERVED unless noted. */
const SECTOR_NAMES = {
  '1':  'Bank',
  '2':  'Cement',
  '3':  'Ceramics',
  '4':  'Corporate Bond',
  '5':  'Debenture',
  '6':  'Engineering',
  '7':  'Financial Institutions',
  '8':  'Food & Allied',
  '9':  'Fuel & Power',
  '10': 'Insurance',
  '11': 'IT',
  '12': 'Jute',
  '13': 'Miscellaneous',
  '14': 'Mutual Funds',
  '15': 'Paper & Printing',
  '16': 'Pharmaceuticals',
  '17': 'Services & Real Estate',
  '18': 'Tannery',
  '19': 'Telecommunication',
  '20': 'Textile',
  '21': 'Travel & Leisure',
  '22': 'Treasury Bond',   /* INFERRED from member symbol T10Y0126 */
  '23': 'Market Index',    /* INFERRED from members DSEX / DS30 / DSES */
  '25': 'Life Insurance',
};

/* Buckets that are not tradable company shares. Kept visible, but never
   mixed into equity breadth, equity turnover or the sector map. */
const NON_EQUITY_SECTORS = { '4': 1, '5': 1, '22': 1, '23': 1 };

/* DSE's own sector-aggregate pseudo-symbols carried in the universe feed.
   Their name IS the sector name, so they need no id lookup. */
function sectorName(raw) {
  if (raw === null || raw === undefined || raw === '') return null;
  const key = String(raw).replace(/\.0+$/, '');
  return SECTOR_NAMES[key] || null;
}
function isNonEquitySector(raw) {
  if (raw === null || raw === undefined || raw === '') return false;
  return !!NON_EQUITY_SECTORS[String(raw).replace(/\.0+$/, '')];
}
/* Match a DSE aggregate row's symbol back to a sector name. */
function sectorFromAggregateSymbol(sym) {
  if (!sym) return null;
  const t = String(sym).trim().toUpperCase();
  const alias = {
    'BANK': 'Bank',
    'CEMENT': 'Cement',
    'CERAMICS SECTOR': 'Ceramics',
    'CORPORATE BOND': 'Corporate Bond',
    'DEBENTURE': 'Debenture',
    'ENGINEERING': 'Engineering',
    'FINANCIAL INSTITUTIONS': 'Financial Institutions',
    'FOOD & ALLIED': 'Food & Allied',
    'FUEL & POWER': 'Fuel & Power',
    'INSURANCE': 'Insurance',
    'IT SECTOR': 'IT',
    'JUTE': 'Jute',
    'LIFE INSURANCE': 'Life Insurance',
    'MISCELLANEOUS': 'Miscellaneous',
    'MUTUAL FUNDS': 'Mutual Funds',
    'PAPER & PRINTING': 'Paper & Printing',
    'PHARMACEUTICALS & CHEMICALS': 'Pharmaceuticals',
    'SERVICES & REAL ESTATE': 'Services & Real Estate',
    'TANNERY INDUSTRIES': 'Tannery',
    'TELECOMMUNICATION': 'Telecommunication',
    'TEXTILE': 'Textile',
    'TRAVEL & LEISURE': 'Travel & Leisure',
  };
  return alias[t] || null;
}

/* ---------------------------------------------------------------------------
   FEATURE DICTIONARY — human phrasing.
   `label`  : what a person calls it.
   `say(v)` : one plain sentence about THIS value. No jargon, no z, no name.
   `dir`    : 'high-notable' | 'low-notable' | 'two-sided'
   The raw name and the number are shown only in Research mode.
   ------------------------------------------------------------------------ */
const FEATURES = {
  rel_volume_z: {
    label: 'Trading activity vs its own normal',
    band: 'z',
    say: v => v >= 3 ? 'Traded enormously more than this stock normally does.'
           : v >= 2 ? 'Volume is unusually high versus this stock’s normal activity.'
           : v >= 1 ? 'Somewhat busier than its usual day.'
           : v <= -1.5 ? 'Much quieter than this stock’s normal activity.'
           : 'Activity is close to its normal level.',
  },
  rel_turnover_z: {
    label: 'Money traded vs its own normal',
    band: 'z',
    say: v => v >= 2 ? 'Far more money changed hands than this stock usually sees.'
           : v >= 1 ? 'More money traded than a typical day.'
           : v <= -1.5 ? 'Much less money traded than usual.'
           : 'Money traded is near its usual level.',
  },
  ret_1: {
    label: 'Price change today',
    band: 'pct',
    say: v => Math.abs(v) < 0.002 ? 'The price finished essentially where it started.'
           : v > 0 ? 'The price closed higher.' : 'The price closed lower.',
  },
  range_pct: {
    label: 'How far it travelled today',
    band: 'pct',
    say: v => v >= 0.06 ? 'It swung across a wide band during the day.'
           : v <= 0.01 ? 'It barely moved between its high and low.'
           : 'It moved within an ordinary daily band.',
  },
  close_location: {
    label: 'Where it closed inside the day’s range',
    band: 'unit',
    say: v => v >= 0.75 ? 'It closed near the top of the day’s range — buyers held on.'
           : v <= 0.25 ? 'It closed near the bottom of the day’s range — sellers held on.'
           : 'It closed in the middle of the day’s range.',
  },
  gap_open: {
    label: 'Opening jump from yesterday',
    band: 'pct',
    say: v => v >= 0.02 ? 'It opened well above yesterday’s close.'
           : v <= -0.02 ? 'It opened well below yesterday’s close.'
           : 'It opened close to yesterday’s close.',
  },
  range_z: {
    label: 'Daily swing vs its own normal',
    band: 'z',
    say: v => v >= 2 ? 'Much wider swing than this stock normally has.'
           : v <= -1.5 ? 'Much narrower swing than normal.'
           : 'Swing is near its usual width.',
  },
  range_compression: {
    label: 'Range tightening',
    band: 'z',
    say: v => v >= 1.5 ? 'The daily range has squeezed unusually tight.'
           : v >= 0.8 ? 'The range is tighter than usual.'
           : 'The range is not compressed.',
  },
  volume_persistence: {
    label: 'How long the activity has lasted',
    band: 'unit',
    say: v => v >= 0.6 ? 'The heavy activity has continued over several sessions.'
           : v >= 0.3 ? 'The activity has held up for a few sessions.'
           : 'The activity is not sustained.',
  },
  activity_concentration: {
    label: 'How bunched the trading was',
    band: 'unit',
    say: v => v >= 0.5 ? 'Most of the day’s trading happened in a few bursts.'
           : 'Trading was spread through the day.',
  },
  realized_vol: {
    label: 'Recent choppiness',
    band: 'pct',
    say: v => v >= 0.05 ? 'It has been very choppy lately.'
           : v <= 0.015 ? 'It has been unusually calm lately.'
           : 'Choppiness is around its normal level.',
  },
  vol_regime_ratio: {
    label: 'Choppiness now vs its longer normal',
    band: 'ratio',
    say: v => v >= 1.5 ? 'It is far more volatile now than its longer-run habit.'
           : v <= 0.7 ? 'It is calmer now than its longer-run habit.'
           : 'Volatility is in its usual regime.',
  },
  amihud_z: {
    label: 'How hard the price is to move',
    band: 'z',
    say: v => v >= 2 ? 'A small amount of money moved the price a lot — thin and fragile.'
           : v >= 1 ? 'The price moves more easily than usual for its size.'
           : v <= -1.5 ? 'The price absorbed trading unusually well — deep for its size.'
           : 'Price impact is around normal.',
  },
  hl_spread_proxy: {
    label: 'Estimated trading cost',
    band: 'pct',
    say: v => v >= 0.05 ? 'The gap between buy and sell prices looks wide.'
           : 'The gap between buy and sell prices looks ordinary.',
  },
  illiquidity_persistence: {
    label: 'How long it has been hard to trade',
    band: 'unit',
    say: v => v >= 0.5 ? 'It has been hard to trade for several sessions running.'
           : 'No sustained trading difficulty.',
  },
  volume_price_divergence: {
    label: 'Volume vs price-move mismatch',
    band: 'z',
    say: v => v >= 2 ? 'Volume was high while the price change was small.'
           : v >= 1 ? 'Volume ran ahead of the size of the price change.'
           : 'Volume and price change are in their usual proportion.',
  },
  accumulation_proxy: {
    /* RENAMED 2026-09-11. The old label asserted a mechanism ("quiet buying")
       that REJECTED_CANDIDATES.md P45-1 tested and rejected. It is a ratio,
       nothing more, and is described as one. */
    label: 'Volume weighted toward up-closes vs down-closes',
    band: 'unit',
    say: v => v >= 0.5 ? 'More of the recent volume printed on up-closes than down-closes.'
           : v <= -0.5 ? 'More of the recent volume printed on down-closes than up-closes.'
           : 'Recent volume is split evenly between up-closes and down-closes.',
  },
  ret_autocorr_1: {
    label: 'Whether moves keep going',
    band: 'unit',
    say: v => v >= 0.2 ? 'Recent moves have tended to continue in the same direction.'
           : v <= -0.2 ? 'Recent moves have tended to snap back.'
           : 'No clear follow-through pattern.',
  },
  abnormal_persistence: {
    label: 'Days it has stayed abnormal',
    band: 'days',
    say: v => v >= 5 ? 'It has been behaving abnormally for many sessions in a row.'
           : v >= 2 ? 'It has been abnormal for more than one session.'
           : v >= 1 ? 'It turned abnormal in this session.'
           : 'Nothing abnormal has persisted.',
  },
  bars_since_abnormal: {
    label: 'Sessions since it was last abnormal',
    band: 'days',
    say: v => v <= 0 ? 'It is abnormal right now.'
           : v <= 3 ? 'It was abnormal within the last few sessions.'
           : 'It has been ordinary for a while.',
  },
  baseline_active_days: {
    label: 'Days of history behind these comparisons',
    band: 'days',
    say: v => v >= 50 ? 'There is a full history behind these comparisons.'
           : v >= 20 ? 'There is a partial history behind these comparisons.'
           : 'Thin history — treat the comparisons with caution.',
  },
  xs_rank_rel_volume: {
    label: 'Activity rank across the market',
    band: 'unit',
    say: v => v >= 0.98 ? 'Among the busiest stocks in the market today.'
           : v >= 0.9 ? 'In the busiest tenth of the market today.'
           : v <= 0.1 ? 'Among the quietest in the market today.'
           : 'Middle of the pack for activity.',
  },
  xs_rank_rel_turnover: {
    label: 'Money-traded rank across the market',
    band: 'unit',
    say: v => v >= 0.98 ? 'Among the largest money flows in the market today.'
           : v >= 0.9 ? 'In the top tenth by money traded.'
           : 'Not among the largest money flows.',
  },
  xs_volume_abnormality: {
    label: 'How unusual it is compared with everyone else',
    band: 'z',
    say: v => v >= 3 ? 'Its activity stands out sharply from the rest of the market.'
           : v >= 1.5 ? 'Its activity stands out from the rest of the market.'
           : 'It is not standing out from the market.',
  },
  market_ret: {
    label: 'What the whole market did',
    band: 'pct',
    say: v => v > 0 ? 'The market as a whole rose.' : v < 0 ? 'The market as a whole fell.' : 'The market was flat.',
  },
  market_relative_ret: {
    label: 'How it did against the market',
    band: 'pct',
    say: v => v >= 0.02 ? 'It clearly outperformed the market today.'
           : v <= -0.02 ? 'It clearly underperformed the market today.'
           : 'It moved roughly with the market.',
  },
  xs_breadth_abnormal: {
    label: 'Share of the market behaving abnormally',
    band: 'unit',
    say: v => v >= 0.1 ? 'A large slice of the market is behaving abnormally.'
           : v >= 0.03 ? 'A handful of stocks across the market are abnormal.'
           : 'The market as a whole is behaving normally.',
  },
  xs_symbols_at_ts: {
    label: 'Stocks compared in this snapshot',
    band: 'count',
    say: v => `This comparison is against ${Math.round(v)} stocks in the same session.`,
  },
};

/* Order features are shown in the evidence layer, grouped the way a person
   would ask about them — not the order they appear in the file. */
const FEATURE_GROUPS = [
  { title: 'What the price did',   keys: ['ret_1', 'range_pct', 'close_location', 'gap_open', 'market_relative_ret'] },
  { title: 'How busy it was',      keys: ['rel_volume_z', 'rel_turnover_z', 'range_z', 'range_compression', 'volume_persistence', 'activity_concentration'] },
  { title: 'How easy to trade',    keys: ['amihud_z', 'hl_spread_proxy', 'illiquidity_persistence'] },
  { title: 'Signs of one-sidedness', keys: ['volume_price_divergence', 'accumulation_proxy', 'ret_autocorr_1'] },
  { title: 'Against the market',   keys: ['xs_rank_rel_volume', 'xs_rank_rel_turnover', 'xs_volume_abnormality', 'market_ret', 'xs_breadth_abnormal'] },
  { title: 'How long it has lasted', keys: ['abnormal_persistence', 'bars_since_abnormal', 'realized_vol', 'vol_regime_ratio', 'baseline_active_days', 'xs_symbols_at_ts'] },
];

/* ---------------------------------------------------------------------------
   OBSERVATIONS — measurements only.

   HARD RULE, added 2026-09-11 after Hridoy caught this layer inventing research:
   the UI states WHAT WAS MEASURED. It never names a mechanism, never tells a
   causal story, never implies a forward outcome. Every row carries the research
   ledger's verdict on that shape so a reader can never mistake a measurement
   for a finding.

   WHAT THE LEDGER ACTUALLY SAYS (SURVIVING_RESEARCH_LEADS.md, 2026-09-06):
     Tradeable candidates: NONE.
     Explicitly NOT carried (REJECTED_CANDIDATES.md §Phase 4.5):
       quiet accumulation, absorption / dip-recovered, closing strength,
       persistence, idiosyncratic moves, every v1 down-door candidate,
       every limit-up footprint.
     Carried, both UNVALIDATED, both awaiting the sealed holdout:
       Lead A  sustained volume departure -> NEGATIVE mean market-relative
               return over h=5-10. Use, if it ever survives, is an AVOIDANCE
               filter. Not a buy signal.
       Lead B  F07: rel_volume_z >= 2 on a day when <=5% of names are
               abnormal-volume -> abnormal up-move within 3 sessions.
               5.60% vs 2.59%. Fails 94% of the time. Margin over plain
               abnormal volume NOT established (t = 0.7).
               The ledger states plainly: "not accumulation (price is not
               calm - the calm variant is weaker)".
   ------------------------------------------------------------------------ */

const LEDGER = {
  headline: 'Research verdict: 0 tradeable candidates. Nothing below is a signal.',
  source: 'SURVIVING_RESEARCH_LEADS.md · REJECTED_CANDIDATES.md · 2026-09-06',
  REJECTED: 'REJECTED — this shape was tested and carries no information',
  UNVALIDATED: 'UNVALIDATED — carried to the sealed holdout, not yet tested there',
  NOT_TESTED: 'NOT TESTED — measured, never put through the falsification battery',
};

/* Each entry restates one measured number. `say` must contain no mechanism,
   no intent, no forward claim. `verdict` is what research says about the
   SHAPE, not about this stock. */
const OBSERVATIONS = [
  {
    id: 'vol_departure',
    tag: 'Volume far from its own normal',
    tone: 'info',
    test: f => num(f.rel_volume_z) >= 2,
    rank: f => num(f.rel_volume_z, 0),
    say: f => `Traded well above this stock's own trailing normal (${F2(f.rel_volume_z)} in log-space robust z).`,
    verdict: 'UNVALIDATED',
    note: 'Closest carried lead is F07 — the same threshold, but only on days when ≤5% of the market is abnormal, and it fails 94% of the time.',
    raw: ['rel_volume_z', 'rel_turnover_z', 'baseline_active_days'],
  },
  {
    id: 'xs_standout',
    tag: 'Stands out across the market',
    tone: 'info',
    test: f => num(f.xs_volume_abnormality) >= 1.5,
    rank: f => num(f.xs_volume_abnormality, 0),
    say: f => `Its activity sits ${F2(f.xs_volume_abnormality)} above the cross-sectional norm for today's session.`,
    verdict: 'NOT_TESTED',
    note: 'Cross-sectional rank was measured, never run through the falsification battery on its own.',
    raw: ['xs_volume_abnormality', 'xs_rank_rel_volume', 'xs_symbols_at_ts'],
  },
  {
    id: 'price_impact',
    tag: 'Large price move per taka traded',
    tone: 'warn',
    test: f => num(f.amihud_z) >= 2,
    rank: f => num(f.amihud_z, 0),
    say: f => `The price moved far for the money that changed hands (Amihud impact ${F2(f.amihud_z)} above its own normal).`,
    verdict: 'NOT_TESTED',
    note: 'A liquidity measurement. No research claim attaches to it.',
    raw: ['amihud_z', 'hl_spread_proxy', 'illiquidity_persistence'],
  },
  {
    id: 'range_wide',
    tag: 'Unusually wide day',
    tone: 'info',
    test: f => num(f.range_z) >= 2,
    rank: f => num(f.range_z, 0),
    say: f => `High-to-low travel was ${F2(f.range_z)} above this stock's own normal range.`,
    verdict: 'NOT_TESTED',
    note: 'Descriptive. The compression variant (P45-7) was UNMEASURABLE at 213 occurrences.',
    raw: ['range_z', 'range_pct', 'realized_vol'],
  },
  {
    id: 'big_move',
    tag: 'Large price change',
    tone: 'neg',
    test: f => Math.abs(num(f.ret_1, 0)) >= 0.05,
    rank: f => Math.abs(num(f.ret_1, 0)) * 40,
    say: f => `Closed ${num(f.ret_1, 0) > 0 ? 'up' : 'down'} ${(Math.abs(f.ret_1) * 100).toFixed(2)}% on the session.`,
    verdict: 'OBSERVED',
    note: 'The published close against the published previous close. Nothing inferred.',
    raw: ['ret_1', 'market_relative_ret', 'close_location'],
  },
];

/* Shapes this UI is FORBIDDEN to display as findings. Kept in code so the ban
   is greppable and a future edit cannot quietly reintroduce them under a new
   name. Hridoy's rule: a rejected idea may not return wearing a new label. */
const FORBIDDEN_CLAIMS = [
  'quiet accumulation', 'accumulation', 'absorption', 'dip recovered',
  'closing strength', 'abnormal persistence', 'distribution',
  'buildup', 'smart money', 'institutional buying', 'breakout',
];

function F2(v) { return (typeof v === 'number' && isFinite(v)) ? v.toFixed(2) : '—'; }

function num(v, dflt) {
  return (typeof v === 'number' && isFinite(v)) ? v : (dflt === undefined ? -Infinity : dflt);
}
