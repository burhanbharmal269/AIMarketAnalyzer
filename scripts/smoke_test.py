from app.sample_data import sample_candidates, sample_market_snapshot, sample_risk_state
from app.services.scanner import scan_market, telegram_text
from app.services.storage import init_db, recent_scans, record_scan


def main():
    market = sample_market_snapshot()
    scan = scan_market(
        sample_candidates(),
        market,
        sample_risk_state(),
        {
            "accountCapital": 500000,
            "riskPercent": 1,
            "maxSpread": 3,
            "minVolume": 20000,
            "eventWindow": 120,
            "lossStreak": 0,
        },
    )

    assert len(scan["approved"]) == 1, scan
    assert scan["approved"][0]["score"]["total"] == 84, scan["approved"][0]["score"]
    assert len(scan["rejected"]) == 3, scan

    no_trade_scan = scan_market(
        sample_candidates(),
        market,
        sample_risk_state(),
        {"lossStreak": 3},
    )
    assert no_trade_scan["noTrade"] is True, no_trade_scan

    init_db()
    record_scan(scan)
    assert recent_scans(1), "expected at least one audit row"
    assert "BANKNIFTY" in telegram_text(scan, market)
    print("Smoke test passed: scanner, no-trade mode, SQLite audit and Telegram text are working.")


if __name__ == "__main__":
    main()

