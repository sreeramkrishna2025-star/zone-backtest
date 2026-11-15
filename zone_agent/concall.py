"""
Concall (earnings-call transcript) agent - DRAFT v1.  Free, no AI key, no orders.

What it does
  1. finds NEW transcripts on NSE for the shortlisted stocks only (Buffett GREEN/YELLOW)   [needs network]
  2. reads the PDF text                                                                   [pypdf / pdftotext]
  3. scores the call with plain RULES (finance word lists + negation handling), optionally
     blended with FinBERT if `transformers` + `torch` are installed                        [FinBERT optional]
  4. keeps one row per call in concall/results.csv (also acts as the "already done" list)

Honest limits: it COUNTS words, it does not understand. "Growth is not weak" is handled (negation),
sarcasm / management spin / long context are not. Treat the verdict as an early signal, not a decision.
Analyst questions (sentences ending in "?") are ignored so the tone mostly reflects management.
All thresholds are in CFG.
"""
from __future__ import annotations

import csv
import io
import os
import re
import subprocess
import tempfile
import time
from datetime import datetime

POS = {
    "growth", "grow", "growing", "grew", "strong", "stronger", "strongest", "robust", "improve", "improved",
    "improvement", "improving", "record", "expand", "expanded", "expansion", "momentum", "healthy", "visibility",
    "outperform", "outperformed", "accelerate", "accelerated", "traction", "resilient", "tailwind", "tailwinds",
    "confident", "optimistic", "opportunity", "opportunities", "pipeline", "upside", "gain", "gains", "gained",
    "efficient", "efficiency", "deleverage", "deleveraged", "debt-free", "encouraging", "beat", "surpass",
    "surpassed", "sustainable", "visible",
}
NEG = {
    "decline", "declined", "declining", "weak", "weaker", "weakness", "pressure", "pressures", "slowdown", "slow",
    "slowing", "slowed", "headwind", "headwinds", "loss", "losses", "impairment", "delay", "delayed", "delays",
    "challenging", "challenge", "challenges", "uncertain", "uncertainty", "volatile", "volatility", "inflationary",
    "shortfall", "deteriorate", "deteriorated", "deterioration", "writeoff", "write-off", "stress", "stressed",
    "default", "defaults", "litigation", "penalty", "disappointing", "muted", "subdued", "soft", "softness",
    "contraction", "contracted", "lowered", "worsened", "worse", "adverse", "downturn", "cautious",
}
UNC = {"may", "might", "could", "uncertain", "uncertainty", "unclear", "depends", "depending", "volatile",
       "volatility", "hopefully", "tentative", "tentatively"}
POS_PHRASES = ["order book", "orderbook", "margin expansion", "market share gain", "debt reduction",
               "better than expected", "ahead of expectations", "price hike"]
NEG_PHRASES = ["margin pressure", "demand slowdown", "one-off loss", "below expectations", "under pressure",
               "lower than expected", "cost pressure", "going concern"]
NEGATORS = {"not", "no", "never", "without", "neither", "nor", "hardly", "barely", "isn't", "wasn't", "aren't",
            "don't", "doesn't", "didn't", "cannot", "can't", "won't"}
GUIDE_CUES = ("guidance", "expect", "target", "aim", "outlook", "going forward", "next year", "next quarter",
              "by fy", "fy27", "fy28", "fy29", "fy30", "cagr", "we plan", "we are planning")
TOPICS = {
    "capex": ("capex", "capital expenditure", "capacity addition", "new plant"),
    "order_book": ("order book", "orderbook", "order inflow", "order backlog"),
    "demand": ("demand",),
    "debt": ("debt", "borrowing", "leverage", "deleverag"),
    "margin": ("margin", "ebitda"),
    "competition": ("competition", "competitive", "price war"),
    "regulation": ("regulat", "policy", "tariff", "duty"),
}

CFG = dict(
    min_signal_words=20,     # pos+neg hits needed, else NODATA ("too little to judge")
    green_tone=0.25,         # tone >= this -> GREEN
    yellow_tone=0.0,         # tone >= this -> YELLOW, below -> RED
    high_uncertainty=8.0,    # uncertainty words per 1000 words above this -> flag
    max_guidance=5,          # guidance snippets kept
    snippet_chars=200,       # each saved snippet is cut to this length
    finbert_weight=0.5,      # share of FinBERT in the final tone (only when it ran)
    finbert_max_sentences=200,
)
ICON = {"GREEN": "🟢", "YELLOW": "🟡", "RED": "🔴", "NODATA": "⚪"}
RESULT_FIELDS = ["symbol", "announced", "pdf_url", "words", "tone", "tone_prev", "tone_change", "pos", "neg",
                 "unc_per_1k", "verdict", "icon", "guidance", "key_lines", "topics", "method", "processed_on"]

_TOKEN = re.compile(r"[a-z][a-z\-']*")
_SPLIT = re.compile(r"(?<=[.!?])\s+|\n{2,}")
_ABBR = re.compile(r"\b(Rs|Mr|Mrs|Ms|Dr|Shri|vs|approx|Inc|Ltd|Pvt|Co|No|Nos|Cr|Mn|Bn)\.(?=\s)", re.I)
_NUM = re.compile(r"(\d+(?:\.\d+)?\s?%|(?:rs\.?|inr|₹)\s?[\d,]+(?:\.\d+)?\s?(?:crore|cr|billion|bn|million|mn)?|"
                  r"\d+(?:\.\d+)?\s?(?:crore|cr|billion|bn)\b)", re.I)


# ----------------------------------------------------------------------------------------- text reading
def extract_text(pdf_bytes: bytes) -> str:
    """PDF -> text. pypdf first, poppler's pdftotext as fallback. Returns '' if both fail."""
    try:
        from pypdf import PdfReader
        r = PdfReader(io.BytesIO(pdf_bytes))
        txt = "\n".join((p.extract_text() or "") for p in r.pages)
        if len(txt.split()) > 50:
            return txt
    except Exception:  # noqa: BLE001
        pass
    try:
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(pdf_bytes)
            name = f.name
        out = subprocess.run(["pdftotext", "-layout", name, "-"], capture_output=True, text=True, timeout=120)
        os.unlink(name)
        return out.stdout
    except Exception:  # noqa: BLE001
        return ""


# ----------------------------------------------------------------------------------------- rules scoring
def _hits(tokens: list[str]) -> tuple[int, int, int]:
    """(pos, neg, unc) for one sentence; a negator within the 3 words before flips a pos/neg word."""
    pos = neg = unc = 0
    for i, w in enumerate(tokens):
        if w in UNC:
            unc += 1
        if w in POS or w in NEG:
            flipped = any(t in NEGATORS for t in tokens[max(0, i - 3):i])
            is_pos = (w in POS) != flipped
            pos += is_pos
            neg += not is_pos
    return pos, neg, unc


def _clip(s: str, n: int) -> str:
    s = re.sub(r"\s+", " ", s).strip()
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def score_text(text: str, cfg: dict = CFG) -> dict:
    """Score one transcript with the rules. Pure function: text in, dict out."""
    text = _ABBR.sub(r"\1", text)                                 # "Rs. 5,000" must not split a sentence
    low = text.lower()
    sentences = [s.strip() for s in _SPLIT.split(text) if len(s.split()) >= 4]
    pos = neg = unc = words = 0
    scored = []                                                   # (net, sentence)
    guidance, topics = [], {k: 0 for k in TOPICS}
    for s in sentences:
        if s.rstrip().endswith("?"):                              # analyst question -> skip
            continue
        toks = _TOKEN.findall(s.lower())
        words += len(toks)
        p, n, u = _hits(toks)
        pos, neg, unc = pos + p, neg + n, unc + u
        if p != n:
            scored.append((p - n, s))
        sl = s.lower()
        if len(guidance) < cfg["max_guidance"] and any(c in sl for c in GUIDE_CUES) and _NUM.search(s):
            snippet = _clip(s, cfg["snippet_chars"])
            if snippet not in guidance:                           # management repeats the same line often
                guidance.append(snippet)
    for ph in POS_PHRASES:
        pos += low.count(ph)
    for ph in NEG_PHRASES:
        neg += low.count(ph)
    for k, keys in TOPICS.items():
        topics[k] = sum(low.count(x) for x in keys)

    if pos + neg < cfg["min_signal_words"] or words < 300:
        return dict(verdict="NODATA", icon=ICON["NODATA"], tone=None, pos=pos, neg=neg, words=words,
                    unc_per_1k=round(unc / max(words, 1) * 1000, 1), guidance=[], key_lines=[], topics=topics,
                    note="too little text to judge")
    tone = round((pos - neg) / (pos + neg), 3)
    scored.sort(key=lambda x: x[0])
    key_lines = [_clip(s, cfg["snippet_chars"]) for _, s in scored[-2:][::-1] if _ > 0]
    key_lines += [_clip(s, cfg["snippet_chars"]) for _, s in scored[:2] if _ < 0]
    out = dict(tone=tone, pos=pos, neg=neg, words=words, unc_per_1k=round(unc / words * 1000, 1),
               guidance=guidance, key_lines=key_lines, topics=topics, note="")
    out.update(_verdict(tone, cfg))
    return out


def _verdict(tone: float, cfg: dict = CFG) -> dict:
    v = "GREEN" if tone >= cfg["green_tone"] else ("YELLOW" if tone >= cfg["yellow_tone"] else "RED")
    return dict(verdict=v, icon=ICON[v])


# ----------------------------------------------------------------------------------------- optional FinBERT
def finbert_tone(text: str, cfg: dict = CFG):
    """Mean (positive - negative) probability over the most 'loaded' management sentences, or None if
    `transformers`/`torch` are missing. NOT run by default (heavy); untested in the sandbox that built it."""
    try:
        from transformers import pipeline
    except Exception:  # noqa: BLE001
        return None
    sents = [s for s in _SPLIT.split(text) if len(s.split()) >= 6 and not s.rstrip().endswith("?")]
    sents.sort(key=lambda s: -abs(_hits(_TOKEN.findall(s.lower()))[0] - _hits(_TOKEN.findall(s.lower()))[1]))
    sents = [_clip(s, 400) for s in sents[: cfg["finbert_max_sentences"]]]
    if not sents:
        return None
    clf = pipeline("text-classification", model="ProsusAI/finbert", top_k=None, truncation=True, max_length=256)
    vals = []
    for res in clf(sents, batch_size=16):
        d = {r["label"].lower(): r["score"] for r in res}
        vals.append(d.get("positive", 0.0) - d.get("negative", 0.0))
    return round(sum(vals) / len(vals), 3)


def analyse(text: str, previous_tone: float | None = None, use_finbert: bool = False, cfg: dict = CFG) -> dict:
    """Rules (+ optional FinBERT) -> one result dict with tone_prev / tone_change / method."""
    r = score_text(text, cfg)
    r["method"] = "rules"
    if use_finbert and r["tone"] is not None:
        fb = finbert_tone(text, cfg)
        if fb is not None:
            r["tone_rules"], r["tone_finbert"] = r["tone"], fb
            r["tone"] = round((1 - cfg["finbert_weight"]) * r["tone"] + cfg["finbert_weight"] * fb, 3)
            r["method"] = "rules+finbert"
            r.update(_verdict(r["tone"], cfg))
    r["tone_prev"] = previous_tone
    r["tone_change"] = (round(r["tone"] - previous_tone, 3)
                        if r["tone"] is not None and previous_tone is not None else None)
    return r


# ----------------------------------------------------------------------------------------- NSE (live)
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
           "Accept": "application/json, text/plain, */*", "Accept-Language": "en-US,en;q=0.9",
           "Referer": "https://www.nseindia.com/"}


class NSE:
    """Small NSE session (cookie handshake + retries). Same idea as tools/fetch_fundamentals.py."""

    def __init__(self):
        import requests
        self._requests = requests
        self.refresh()

    def refresh(self):
        self.s = self._requests.Session()
        try:
            self.s.get("https://www.nseindia.com", headers=HEADERS, timeout=20)
        except Exception:  # noqa: BLE001
            pass

    def get(self, url: str, as_json: bool = False, raw: bool = False):
        for k in range(3):
            try:
                r = self.s.get(url, headers=HEADERS, timeout=40)
                if r.status_code == 200:
                    return r.content if raw else (r.json() if as_json else r.text)
                if r.status_code in (401, 403):
                    self.refresh()
            except Exception:  # noqa: BLE001
                pass
            time.sleep(2 + 3 * k)
        return None


def fetch_announcements(nse: NSE, day_from: datetime, day_to: datetime) -> list[dict]:
    """ALL equity announcements in the date range, in ONE call (cheap; filtered afterwards)."""
    url = ("https://www.nseindia.com/api/corporate-announcements?index=equities"
           f"&from_date={day_from:%d-%m-%Y}&to_date={day_to:%d-%m-%Y}")
    data = nse.get(url, as_json=True)
    if isinstance(data, dict):
        data = data.get("data") or []
    return data if isinstance(data, list) else []


def pick_transcripts(records: list[dict], symbols: set[str], done_urls: set[str]) -> list[dict]:
    """Transcript PDFs of shortlisted symbols that are not processed yet. Field names are read tolerantly
    because they could not be checked live when this was written: run `run_concall.py --probe` once."""
    out, seen = [], set()
    for rec in records:
        text = " ".join(str(rec.get(k, "")) for k in ("desc", "attchmntText", "subject", "sm_name", "headline")).lower()
        url = str(rec.get("attchmntFile") or rec.get("attachment") or rec.get("attchmnt") or "")
        sym = str(rec.get("symbol") or "").upper()
        if "transcript" not in text or not url.lower().endswith(".pdf") or sym not in symbols:
            continue
        if url in done_urls or url in seen:
            continue
        seen.add(url)
        out.append({"symbol": sym, "pdf_url": url,
                    "announced": str(rec.get("an_dt") or rec.get("sort_date") or rec.get("exchdisstime") or "")[:20]})
    return out


# ----------------------------------------------------------------------------------------- saved results
def load_results(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    return list(csv.DictReader(open(path, encoding="utf-8")))


def previous_tone(rows: list[dict], symbol: str) -> float | None:
    """Tone of the latest earlier call of this stock (by announced date text order, then file order)."""
    for r in reversed(rows):
        if r.get("symbol") == symbol and r.get("tone") not in (None, ""):
            try:
                return float(r["tone"])
            except ValueError:
                pass
    return None


def to_row(symbol: str, announced: str, pdf_url: str, res: dict) -> dict:
    return {"symbol": symbol, "announced": announced, "pdf_url": pdf_url, "words": res.get("words"),
            "tone": "" if res.get("tone") is None else res["tone"],
            "tone_prev": "" if res.get("tone_prev") is None else res["tone_prev"],
            "tone_change": "" if res.get("tone_change") is None else res["tone_change"],
            "pos": res.get("pos"), "neg": res.get("neg"), "unc_per_1k": res.get("unc_per_1k"),
            "verdict": res["verdict"], "icon": res["icon"], "guidance": " | ".join(res.get("guidance", [])),
            "key_lines": " | ".join(res.get("key_lines", [])),
            "topics": ";".join(f"{k}={v}" for k, v in res.get("topics", {}).items() if v),
            "method": res.get("method", "rules"), "processed_on": datetime.now().strftime("%Y-%m-%d")}


def save_results(path: str, rows: list[dict]) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=RESULT_FIELDS)
        w.writeheader()
        w.writerows(rows)
