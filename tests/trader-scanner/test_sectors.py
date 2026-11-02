"""Offline tests for the sector / industry / exchange filter of trader-scanner.

    python3 -m unittest discover -s tests/trader-scanner
"""
import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr

HERE = os.path.dirname(os.path.abspath(__file__))
SCAN = os.path.join(HERE, "..", "..", ".claude", "skills", "trader-scanner", "scan.py")
spec = importlib.util.spec_from_file_location("scan", SCAN)
scan = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scan)

NASDAQ = """Symbol|Security Name|Market Category|Test Issue|Financial Status|Round Lot Size|ETF|NextShares
AAPL|Apple Inc. - Common Stock|Q|N|N|100|N|N
NVDA|NVIDIA Corporation - Common Stock|Q|N|N|100|N|N
AMGN|Amgen Inc. - Common Stock|Q|N|N|100|N|N
NEWT|Newtech Corp. - Class A Common Stock|G|N|N|100|N|N
QQQ|Invesco QQQ Trust|G|N|N|100|Y|N
File Creation Time: 1102202608:00|||||||
"""
OTHER = """ACT Symbol|Security Name|Exchange|CQS Symbol|ETF|Round Lot Size|Test Issue|NASDAQ Symbol
IBM|International Business Machines Corporation Common Stock|N|IBM|N|100|N|IBM
BRK.B|Berkshire Hathaway Inc. Class B Common Stock|N|BRK.B|N|100|N|BRK.B
XYZT|Xyz Tech Inc. Common Stock|A|XYZT|N|100|N|XYZT
File Creation Time: 1102202608:00|||||||
"""
SCREENER = {"data": {"asOf": None, "headers": {}, "rows": [
    {"symbol": "AAPL", "sector": "Technology", "industry": "Computer Manufacturing"},
    {"symbol": "NVDA", "sector": "Technology", "industry": "Semiconductors"},
    {"symbol": "AMGN", "sector": "Health Care", "industry": "Biotechnology: Biological Products"},
    {"symbol": "NEWT", "sector": "", "industry": ""},
    {"symbol": "IBM", "sector": "Technology", "industry": "Computer Manufacturing"},
    {"symbol": "BRK/B", "sector": "Finance", "industry": "Property-Casualty Insurers"},
]}}
SEC_TICKERS = {"fields": ["cik", "name", "ticker", "exchange"],
               "data": [[111, "Newtech Corp.", "NEWT", "Nasdaq"]]}
SEC_NEWT = {"sic": "7372", "sicDescription": "Services-Prepackaged Software"}


def load(path):
    with open(path) as fh:
        return json.load(fh)


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = scan.main(argv)
    return code, out.getvalue(), err.getvalue()


class SectorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = self.d = self.tmp.name
        for name, text in (("nasdaqlisted.txt", NASDAQ), ("otherlisted.txt", OTHER)):
            with open(os.path.join(d, name), "w") as fh:
                fh.write(text)
        with open(os.path.join(d, "screener.json"), "w") as fh:
            json.dump(SCREENER, fh)
        with open(os.path.join(d, "company_tickers_exchange.json"), "w") as fh:
            json.dump(SEC_TICKERS, fh)
        with open(os.path.join(d, "CIK%010d.json" % 111), "w") as fh:
            json.dump(SEC_NEWT, fh)
        self.uni = os.path.join(d, "universe.txt")
        self.sec = os.path.join(d, "sectors.json")

    def tearDown(self):
        self.tmp.cleanup()

    def universe(self, *extra):
        return run(["universe", "--offline", self.d, "--out", self.uni, "--sectors-out", self.sec] + list(extra))

    def test_universe_writes_exchange_and_sector(self):
        code, out, _ = self.universe()
        self.assertEqual(code, 0)
        doc = load(self.sec)
        s = doc["symbols"]
        self.assertNotIn("QQQ", s)
        self.assertEqual(s["AAPL"]["exchange"], "nasdaq")
        self.assertEqual(s["IBM"]["exchange"], "nyse")
        self.assertEqual(s["XYZT"]["exchange"], "nyse-american")
        self.assertEqual(s["BRK.B"]["sector"], "Finance")       # BRK/B -> BRK.B
        self.assertIsNone(s["NEWT"]["sector"])                  # blank in the screener, no guess
        self.assertIsNone(s["XYZT"]["sector"])                  # missing from the screener
        self.assertEqual(doc["counts"]["no_sector"], 2)
        self.assertIn("2 without a sector", out)

    def test_sec_fallback_fills_tech_sic(self):
        code, out, _ = self.universe("--sec-fallback")
        self.assertEqual(code, 0)
        s = load(self.sec)["symbols"]
        self.assertEqual(s["NEWT"]["sector"], "Technology")
        self.assertEqual(s["NEWT"]["source"], "sec-sic")
        self.assertEqual(s["NEWT"]["sic"], "7372")
        self.assertIsNone(s["XYZT"]["sector"])                  # not in the SEC file either

    def test_filter_nasdaq_tech(self):
        self.universe()
        table = load(self.sec)["symbols"]
        syms = scan.symbols_from_file(self.uni)
        kept, c = scan.filter_by_sector(syms, table, ["tech"], None, ["nasdaq"])
        self.assertEqual(kept, ["AAPL", "NVDA"])
        self.assertEqual(c["unknown"], 1)                       # NEWT: Nasdaq, no sector
        kept, _ = scan.filter_by_sector(syms, table, ["Technology"], ["semiconductor"], None)
        self.assertEqual(kept, ["NVDA"])
        kept, _ = scan.filter_by_sector(syms, table, None, None, ["amex"])
        self.assertEqual(kept, ["XYZT"])

    def test_unknown_symbol_is_left_out(self):
        kept, c = scan.filter_by_sector(["ZZZZ"], {}, ["Technology"])
        self.assertEqual((kept, c["unknown"]), ([], 1))

    def test_screener_csv_file(self):
        csvp = os.path.join(self.d, "nasdaq_screener.csv")
        with open(csvp, "w") as fh:
            fh.write("\ufeffSymbol,Name,Last Sale,Net Change,% Change,Market Cap,Country,IPO Year,Volume,Sector,Industry\n"
                     "AAPL,Apple Inc. Common Stock,$344.63,-4.28,-1.227%,5029592253400,United States,1980,1,"
                     "Technology,Computer Manufacturing\n"
                     "BRK/B,Berkshire Hathaway,$1,0,0%,1,United States,,1,Finance,Property-Casualty Insurers\n")
        os.remove(os.path.join(self.d, "screener.json"))
        code, out, _ = self.universe("--screener-file", csvp)
        self.assertEqual(code, 0)
        s = load(self.sec)["symbols"]
        self.assertEqual(s["AAPL"]["sector"], "Technology")
        self.assertEqual(s["BRK.B"]["sector"], "Finance")
        self.assertIsNone(s["NVDA"]["sector"])

    def test_sec_only_when_screener_missing(self):
        os.remove(os.path.join(self.d, "screener.json"))
        code, out, err = self.universe("--sec-fallback")
        self.assertEqual(code, 0)
        s = load(self.sec)["symbols"]
        self.assertEqual(s["NEWT"]["sector"], "Technology")
        self.assertIsNone(s["AAPL"]["sector"])                  # not in the SEC fixture: unknown
        self.assertIn("screener unavailable", out)

    def test_no_screener_no_sec_writes_nothing(self):
        os.remove(os.path.join(self.d, "screener.json"))
        code, out, _ = self.universe()
        self.assertEqual(code, 0)
        self.assertFalse(os.path.exists(self.sec))
        self.assertIn("--screener-file", out)

    def test_sic_ranges(self):
        self.assertEqual(scan.sic_sector("3674"), "Technology")  # semiconductors
        self.assertEqual(scan.sic_sector("7372"), "Technology")
        self.assertIsNone(scan.sic_sector("2834"))               # pharmaceuticals
        self.assertIsNone(scan.sic_sector(None))

    def test_scan_reports_the_filter(self):
        self.universe()
        code, out, err = run(["daily", "--universe", self.uni, "--sectors", self.sec, "--sector", "Technology",
                              "--exchange", "nasdaq", "--offline", self.d + "/none.json", "--no-write"])
        self.assertEqual(code, 1)                               # no offline charts: refused, not invented
        self.assertIn("no offline file", err)

    def test_empty_filter_lists_sectors(self):
        self.universe()
        code, _, err = run(["orb", "--universe", self.uni, "--sectors", self.sec, "--sector", "Energy",
                            "--now", "2026-10-19 10:15", "--no-write"])
        self.assertEqual(code, 1)
        self.assertIn("no symbol left", err)
        self.assertIn("Technology", err)

    def test_missing_sectors_file(self):
        code, _, err = run(["daily", "--symbols", "AAPL", "--sector", "Technology",
                            "--sectors", os.path.join(self.d, "nope.json"), "--no-write"])
        self.assertEqual(code, 1)
        self.assertIn("scan.py universe", err)


if __name__ == "__main__":
    unittest.main()
