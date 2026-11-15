# Buffett agent (Claude C) — DRAFT v1

**Rules abhi final nahi hain.** Owner Somvar ko backtest ke baad tay karega. Saari limits ek hi jagah hain: `CFG` in `zone_agent/buffett.py`.

Ye sirf analysis karta hai. Koi order nahi, koi network nahi, koi secret nahi: sirf CSV padhta hai.

## Chalana
```
python tools/run_buffett.py                      # sirf quality (price ke bina)
python tools/run_buffett.py --prices prices.csv  # + P/E, PEG, near 52W high  (columns: symbol,close[,high52])
python tests/test_buffett.py                     # 9 tests
```
Nateeja: `buffett/scores_latest.csv` (AMFI top-1000, jin ke NSE symbol hain).

## Rules (draft)
| Hissa | Check | Points |
|---|---|---|
| Quality | EPS (TTM) pichhle <=3 saalon me kitni baar badha | 25 |
| Quality | Pichhle 8 quarter me loss nahi | 10 |
| Quality | Net margin saal bhar me 2 point se zyada nahi giri | 10 |
| Quality | Latest TTM EPS growth >= 10% | 15 |
| Price (sirf price dene par) | PEG = P/E / EPS CAGR; <=1.5 full, <=2.5 aadha | 40 |

- Verdict: 🟢 >= 70%, 🟡 >= 45%, 🔴 baaki. TTM EPS <= 0 ya 2+ loss quarter = seedha 🔴.
- ⚪ `NODATA`: data nahi / bahut chhota / purana.
- Flags (verdict nahi badalte): `near 52W high` (mehenga ho sakta hai), `standalone results only`.

## Abhi ki kamzoriyan (sach)
- Top-1000 me se 964 ke NSE symbol hain. Unme se 665 ke paas fundamentals data hai, par **604** ka hi score ban paya (baaki ka data chhota ya purana). Baaki 360 ⚪ (jaise BHARTIARTL: data hai hi nahi).
- **~333 stocks ka data standalone hai**, group (consolidated) ka nahi. Bade group (jaise RELIANCE) ka score galat ho sakta hai, isliye reasons me flag likha hai.
- ROE, ROCE, karz (debt), cash flow ka data nahi. Ye Hissa 2 (Upstox) me aayega.
- Price ke bina daam nahi dekha jaata. App ke FYERS daily data se `prices.csv` banega (Claude A ka kaam).
- AMFI list ki sabse nayi period abhi `2025-12-31` hai.
