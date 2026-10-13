# Results

Runs: 12 | screens: 6 | SDS commit: 05525f7

| Metric | baseline | rules | Figma reference code |
|---|---|---|---|
| Component reuse | 0.321 | 0.991 | 1.0 |
| Layout via SDS layout | 0.274 | 0.987 | 1.0 |
| Token fidelity | 0.192 | 0.833 | 1.0 |
| Recall of reference components | 0.0 | 0.691 |  |
| Hard-coded values / screen | 58.333 | 0.167 | 0 |
| One-off components / screen | 0.5 | 0.167 | 0 |
| Raw HTML replacing SDS components / screen | 13.333 | 0 | 0 |
| Files edited outside src/screens | 0 | 0 |  |
| Cost per screen (USD) | 0.239 | 0.262 |  |
| Agent turns | 16.833 | 19.5 |  |

## Per screen (component reuse, mean over runs)

| Screen | baseline | rules |
|---|---|---|
| About | 0.926 | 1.0 |
| ContactUs | 1.0 | 1.0 |
| HomePage | 0.0 | 1.0 |
| LandingPage | 0.0 | 1.0 |
| Pricing | 0.0 | 0.947 |
| Waitlist | 0.0 | 1.0 |

Invented tokens (var(--sds-*) that do not exist): none
Hallucinated SDS imports (names SDS does not export): none
