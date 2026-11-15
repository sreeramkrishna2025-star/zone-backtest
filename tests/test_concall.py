"""
Plain-assert tests for zone_agent/concall.py (no pytest, no network, no FinBERT):  python tests/test_concall.py
Uses made-up transcript text only.
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from zone_agent import concall as C  # noqa: E402

GOOD = [
    "We saw strong growth this quarter and our order book is at a record level.",
    "Margins improved and we are confident about the demand outlook for the coming quarters.",
    "The business remains resilient with healthy visibility and robust traction in new products.",
    "We expect revenue growth of 18% next year and we aim to reach Rs. 5,000 crore by FY28.",
    "Efficiency gains and margin expansion gave us a strong momentum across segments.",
]
BAD = [
    "Demand was weak and margin pressure continued with a delay in several projects.",
    "We faced headwinds, a decline in volumes and challenging conditions in export markets.",
    "There was a loss in one subsidiary and an impairment on an old asset.",
    "Uncertainty remains high and the outlook is subdued with volatile raw material prices.",
    "Costs were under pressure and softness in demand lowered our profits.",
]
FILLER = "The company operates across several regions and serves a broad base of customers in many industries. "


def transcript(lines, reps=12):
    body = []
    for _ in range(reps):
        body += lines + [FILLER]
    body.insert(3, "What is your view on the pricing environment and is growth weak in the next quarter?")
    return " ".join(body)


def test_positive_call_is_green_with_guidance():
    r = C.score_text(transcript(GOOD))
    assert r["verdict"] == "GREEN" and r["tone"] > 0.5, r
    assert r["guidance"] and any("18%" in g or "5,000" in g for g in r["guidance"])
    assert r["topics"]["order_book"] > 0


def test_negative_call_is_red():
    r = C.score_text(transcript(BAD))
    assert r["verdict"] == "RED" and r["tone"] < -0.3, r
    assert r["key_lines"]


def test_negation_flips_meaning():
    assert C._hits(C._TOKEN.findall("the demand is not weak at all"))[:2] == (1, 0)      # "not weak" = positive
    assert C._hits(C._TOKEN.findall("there was no growth this quarter"))[:2] == (0, 1)   # "no growth" = negative
    assert C._hits(C._TOKEN.findall("growth is not weak at all"))[:2] == (2, 0)          # both parts are positive


def test_analyst_question_is_ignored():
    q = "Is the decline in margins a headwind and is the weak demand a challenge for you?"
    base = C.score_text(transcript(GOOD))
    r = C.score_text(transcript(GOOD) + " " + q * 30)
    assert abs(r["tone"] - base["tone"]) < 0.05


def test_thin_text_is_nodata():
    r = C.score_text("Good morning everyone and thank you for joining. We had a strong quarter.")
    assert r["verdict"] == "NODATA" and r["tone"] is None


def test_tone_change_vs_previous_call():
    r = C.analyse(transcript(BAD), previous_tone=0.6)
    assert r["tone_change"] < -1.0 and r["method"] == "rules"
    assert C.analyse(transcript(GOOD))["tone_change"] is None


def test_pick_transcripts_filters_and_skips_done():
    recs = [
        {"symbol": "TCS", "desc": "Analysts/Institutional Investor Meet/Con. Call Updates",
         "attchmntText": "Transcript of the Q2 earnings call", "attchmntFile": "https://x/a.pdf", "an_dt": "14-Nov-2026 18:00:00"},
        {"symbol": "TCS", "desc": "Analysts/Institutional Investor Meet/Con. Call Updates",
         "attchmntText": "Audio recording of earnings call", "attchmntFile": "https://x/b.pdf"},          # no transcript
        {"symbol": "XYZ", "attchmntText": "Transcript of call", "attchmntFile": "https://x/c.pdf"},        # not shortlisted
        {"symbol": "INFY", "attchmntText": "Transcript of call", "attchmntFile": "https://x/d.pdf"},      # already done
        {"symbol": "TCS", "attchmntText": "Transcript of call", "attchmntFile": "https://x/e.zip"},       # not a pdf
        {"symbol": "TCS", "attchmntText": "Transcript of call", "attchmntFile": "https://x/a.pdf"},       # duplicate url
    ]
    got = C.pick_transcripts(recs, {"TCS", "INFY"}, {"https://x/d.pdf"})
    assert [g["pdf_url"] for g in got] == ["https://x/a.pdf"] and got[0]["symbol"] == "TCS"


def _mini_pdf(text: str) -> bytes:
    """Smallest valid one-page text PDF (so extract_text can be tested without any file)."""
    lines = [text[i:i + 90] for i in range(0, len(text), 90)]
    stream = "BT /F1 9 Tf 20 800 Td 11 TL " + " ".join(f"({l.replace('(', '').replace(')', '')}) Tj T*" for l in lines) + " ET"
    objs = ["<< /Type /Catalog /Pages 2 0 R >>", "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 5 0 R /Resources << /Font << /F1 4 0 R >> >> >>",
            "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
            f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream"]
    out, offs = b"%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offs.append(len(out))
        out += f"{i} 0 obj\n{o}\nendobj\n".encode("latin-1")
    x = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offs)
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{x}\n%%EOF".encode()
    return out


def test_pdf_text_is_extracted():
    text = " ".join(GOOD * 3)
    got = C.extract_text(_mini_pdf(text))
    assert "order book" in got and "strong growth" in got


def test_results_roundtrip_and_previous_tone():
    res = C.analyse(transcript(GOOD))
    rows = [C.to_row("TCS", "14-Nov-2026", "https://x/a.pdf", res)]
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "r", "results.csv")
        C.save_results(p, rows)
        back = C.load_results(p)
    assert back[0]["symbol"] == "TCS" and back[0]["verdict"] == "GREEN"
    assert C.previous_tone(back, "TCS") == res["tone"] and C.previous_tone(back, "INFY") is None


def test_rs_abbreviation_does_not_split_guidance():
    r = C.score_text(transcript(GOOD))
    assert any("Rs 5,000 crore by FY28" in g for g in r["guidance"]), r["guidance"]


def test_repeated_guidance_is_kept_once():
    r = C.score_text(transcript(GOOD, reps=30))
    assert len(r["guidance"]) == len(set(r["guidance"]))


def test_finbert_is_optional():
    r = C.analyse(transcript(GOOD), use_finbert=True)       # transformers may be missing -> falls back to rules
    assert r["method"] in {"rules", "rules+finbert"} and r["verdict"] == "GREEN"


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for f in fns:
        f()
        print("OK ", f.__name__)
    print(f"{len(fns)} tests passed")
