# Formula Model Lab - Enhanced Features Guide

## Overview

The Formula Model Lab allows you to create weighted prediction models using multiple features. As of this enhancement, the model supports **8 weighted features** (up from 4).

## Feature Categories

### Core Metrics (Legacy Features)

1. **Torvik AdjEM** (Default: 40%)
   - Calculation: `home_torvik_adj_em - away_torvik_adj_em`
   - Purpose: Overall team quality rating

2. **Recent (L7)** (Default: 20%)
   - Calculation: `home_netrtg_l7_pre - away_netrtg_l7_pre`
   - Purpose: Recent form and momentum

3. **Four Factors** (Default: 12%)
   - Calculation: `(eFG% - TOV% + ORB% + FTR) * 10`
   - Purpose: Comprehensive offensive efficiency

4. **SOS Weighted** (Default: 8%)
   - Calculation: `(home_sos - away_sos) / 10`
   - Purpose: Adjust for opponent quality

### Advanced Metrics (New Features)

5. **Defensive Efficiency** (Default: 8%)
   - Calculation: `away_drtg_l7_pre - home_drtg_l7_pre`
   - Purpose: Defensive quality gap

6. **Offensive Efficiency** (Default: 6%)
   - Calculation: `home_ortg_l7_pre - away_ortg_l7_pre`
   - Purpose: Offensive firepower differential

7. **Tempo Advantage** (Default: 4%)
   - Calculation: `(home_pace - away_pace) * 0.15`
   - Purpose: Game speed impact

8. **Three-Point Rate** (Default: 2%)
   - Calculation: `(home_3par - away_3par) * 20`
   - Purpose: 3-point shooting style

## Usage

All weights are normalized to sum to 1.0. Create models through the UI or API:

```python
params = {
    "weights": {
        "torvik_adjem": 0.40,
        "recent_netrtg": 0.20,
        "four_factors": 0.12,
        "sos_weighted": 0.08,
        "def_efficiency": 0.08,
        "off_efficiency": 0.06,
        "tempo_advantage": 0.04,
        "three_rate": 0.02,
    },
    "hca_mode": "static",
    "hca_static_value": 2.7,
    "pace_adjustment": True
}
```

## Backwards Compatibility

Legacy 4-feature models continue to work without modification.
