# Fit-score eval report

- Paired predictions: **23** (unpaired: 0)
- **Spearman ρ:** 0.913  *(rank agreement — does the model order districts like a human?)*
- **Pearson r:** 0.924  *(linear agreement on raw score values)*
- **MAE:** 8.39 points  *(mean absolute error on 0-100 scale)*
- **RMSE:** 10.39 points
- **Tier accuracy:** 87.0%  *(strong/moderate/weak bucket match)*

## Tier confusion

|              | pred strong | pred moderate | pred weak |
|--------------|:-----------:|:-------------:|:---------:|
| label strong   |           8 |             2 |         0 |
| label moderate |           1 |             7 |         0 |
| label weak     |           0 |             0 |         5 |

## Per-district

| District | Name | Labeled | Predicted | Δ | Match? |
|---|---|---:|---:|---:|:---:|
| D011 | Salt Lake County School District | 90 | 91 | +1 | ✓ |
| D020 | Kearney Public Schools | 85 | 91 | +6 | ✓ |
| D001 | Larkspur Unified School District | 72 | 88 | +16 | ✓ |
| D023 | Grandview ISD | 88 | 82 | -6 | ✓ |
| D004 | Maple Ridge School District 142 | 74 | 78 | +4 | ✓ |
| D009 | Brookhaven Public Schools | 74 | 78 | +4 | ✓ |
| D022 | Bayou Parish Schools | 84 | 78 | -6 | ✓ |
| D029 | Westbrook City Schools | 58 | 78 | +20 | ✗ |
| D030 | Magnolia Heights School District | 82 | 78 | -4 | ✓ |
| D026 | North Cascade Public Schools | 65 | 74 | +9 | ✓ |
| D002 | Hartwell County Schools | 62 | 72 | +10 | ✓ |
| D019 | Whispering Pines USD | 48 | 72 | +24 | ✓ |
| D028 | Lone Star ISD | 70 | 72 | +2 | ✗ |
| D005 | Greenfield Township Public Schools | 70 | 68 | -2 | ✗ |
| D012 | Cedar Falls Community Schools | 52 | 62 | +10 | ✓ |
| D021 | Edgewater School District | 54 | 62 | +8 | ✓ |
| D027 | Plainsboro USD | 48 | 62 | +14 | ✓ |
| D015 | Hampton Roads Public Schools | 58 | 52 | -6 | ✓ |
| D003 | Rio Verde ISD | 35 | 35 | +0 | ✓ |
| D014 | SD #74 (Mission) | 22 | 28 | +6 | ✓ |
| D008 | Mountain View ISD | 35 | 25 | -10 | ✓ |
| D018 | Buckeye Online Learning Co. | 12 | 5 | -7 | ✓ |
| D025 | Rocky Mountain Community College | 18 | 0 | -18 | ✓ |
