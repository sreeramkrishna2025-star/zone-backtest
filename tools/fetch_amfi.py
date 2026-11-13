"""
Download AMFI half-yearly 'Average Market Capitalization of listed companies' lists and build
data/amfi/ranks.csv with columns: period_end, rank, company, isin, nse_symbol, bse_code, category.
"""
import io
import os
import re
from datetime import date

import pandas as pd
import requests

H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36"}
BASE = "https://www.amfiindia.com/Themes/Theme1/downloads/"
OUT = "data/amfi"
MON = {6: ["Jun", "June", "JUNE", "jun"], 12: ["Dec", "December", "DEC", "dec"]}


def candidates(y, m):
    d = 30 if m == 6 else 31
    names = []
    for mo in MON[m]:
        for stem in ("AverageMarketCapitalizationoflistedcompaniesduringthesixmonthsended",
                     "AverageMarketCapitalization", "Average%20Market%20Capitalization%20of%20listed%20companies%20during%20the%20six%20months%20ended%20"):
            for ds in (f"{d}{mo}{y}", f"{d}{mo}{y}".lower(), f"{d}%20{mo}%20{y}", f"{d}{mo}{y}_0"):
                for ext in (".xlsx", ".xls"):
                    names.append(BASE + stem + ds + ext)
    return names


def page_links(s):
    links = set()
    for url in ("https://www.amfiindia.com/research-information/other-data/categorization-of-stocks",
                "https://www.amfiindia.com/research-information/other-data/categorization-of-stocks?Year="):
        try:
            t = s.get(url, headers=H, timeout=30).text
            links |= set(re.findall(r'href="([^"]*AverageMarketCap[^"]*\.xlsx?)"', t, re.I))
        except Exception as e:  # noqa: BLE001
            print("page error", e)
    return {(l if l.startswith("http") else "https://www.amfiindia.com" + l) for l in links}


def parse(content, period_end):
    x = pd.read_excel(io.BytesIO(content), header=None)
    hdr = next(i for i in range(min(15, len(x))) if x.iloc[i].astype(str).str.contains("ISIN", case=False).any())
    x.columns = [str(c).strip() for c in x.iloc[hdr]]
    x = x.iloc[hdr + 1:]

    def col(*keys):
        for c in x.columns:
            if all(k.lower() in c.lower() for k in keys):
                return c
        return None

    out = pd.DataFrame({
        "period_end": period_end,
        "rank": pd.to_numeric(x[col("Sr")] if col("Sr") else range(1, len(x) + 1), errors="coerce"),
        "company": x[col("Company")] if col("Company") else "",
        "isin": x[col("ISIN")],
        "nse_symbol": x[col("NSE", "Symbol")] if col("NSE", "Symbol") else "",
        "bse_code": x[col("BSE", "Symbol")] if col("BSE", "Symbol") else "",
        "category": x[col("Categorization")] if col("Categorization") else "",
    })
    return out[out["rank"].notna()]


def main():
    os.makedirs(OUT, exist_ok=True)
    s = requests.Session()
    found = page_links(s)
    print("links on AMFI page:", len(found))
    frames = []
    for y in range(2017, date.today().year + 1):
        for m in (6, 12):
            pe = date(y, m, 30 if m == 6 else 31)
            if pe > date.today() or pe < date(2017, 12, 31):
                continue
            tag = f"{pe:%d%b%Y}"
            urls = [u for u in found if tag.lower() in u.lower().replace("june", "jun").replace("december", "dec")]
            urls += candidates(y, m)
            got = None
            for u in urls:
                try:
                    r = s.get(u, headers=H, timeout=40)
                    if r.status_code == 200 and len(r.content) > 20000 and r.content[:2] in (b"PK", b"\xd0\xcf"):
                        got = (u, r.content)
                        break
                except Exception:  # noqa: BLE001
                    pass
            if not got:
                print(pe, "NOT FOUND")
                continue
            try:
                df = parse(got[1], pe.isoformat())
                frames.append(df)
                print(pe, "ok", len(df), "companies", got[0].split("/")[-1])
            except Exception as e:  # noqa: BLE001
                print(pe, "parse error", e, got[0])
    if frames:
        allr = pd.concat(frames, ignore_index=True)
        allr.to_csv(f"{OUT}/ranks.csv", index=False)
        print("saved", len(allr), "rows")


if __name__ == "__main__":
    main()
