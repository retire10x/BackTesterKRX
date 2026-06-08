"""
KIS 게이트웨이 연동 테스트 (잔고 조회 · 선택적 주문).

  # DRY-RUN (기본, .env LIVE_DRY_RUN=1)
  python scripts/test_live_gateway.py

  # 모의투자 API 실연동 (잔고만)
  python scripts/test_live_gateway.py --live

  # 모의투자 + 삼성전자 매수 시도 (장중·주의)
  python scripts/test_live_gateway.py --live --order
"""
from __future__ import annotations

import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def _load_dotenv() -> None:
    path = os.path.join(ROOT, ".env")
    if not os.path.isfile(path):
        return
    with open(path, encoding="utf-8") as fh:
        for raw in fh.read().splitlines():
            s = raw.strip()
            if not s or s.startswith("#") or "=" not in s:
                continue
            k, v = s.split("=", 1)
            key, val = k.strip(), v.strip().strip("\"'")
            if key and key not in os.environ:
                os.environ[key] = val


def main() -> None:
    p = argparse.ArgumentParser(description="KIS LiveAccountGateway 테스트")
    p.add_argument("--live", action="store_true", help="LIVE_DRY_RUN=0 으로 실제 API 호출")
    p.add_argument("--order", action="store_true", help="삼성전자(005930) 매수 시도 (위험)")
    p.add_argument("--real", action="store_true", help="live_settings mode=real_money (비권장)")
    p.add_argument("--debug", action="store_true", help="잔고 API raw output2 일부 출력")
    args = p.parse_args()

    _load_dotenv()
    if args.live:
        os.environ["LIVE_DRY_RUN"] = "0"

    from src.live.live_config import load_live_config
    from src.live.live_account import LiveAccountGateway

    cfg = load_live_config()
    if args.real:
        from dataclasses import replace

        cfg = replace(
            cfg,
            account=replace(cfg.account, mode="real_money"),
        )

    gw = LiveAccountGateway(cfg.account)
    print(f"mode={gw.mode} dry_run={gw.dry_run} account={gw.account_number}-{gw.account_code}")

    snap = None
    if args.debug and not gw.dry_run:
        try:
            raw = gw._raw_balance()
            print("=== API raw ===")
            print(f"  rt_cd={raw.get('rt_cd')} msg1={raw.get('msg1')}")
            o2 = (raw.get("output2") or [{}])[0]
            for k in ("dnca_tot_amt", "ord_psbl_cash", "prvs_rcdl_excc_amt", "nass_amt", "tot_evlu_amt"):
                if k in o2:
                    print(f"  {k}={o2[k]}")
            print("  output2 keys:", ", ".join(sorted(o2.keys())[:12]), "...")
            from src.live.live_account import AccountSnapshot, LivePosition

            holdings: list[LivePosition] = []
            for item in raw.get("output1") or []:
                qty = int(item.get("hldg_qty") or item.get("ccld_qty") or 0)
                if qty <= 0:
                    continue
                code = str(item.get("pdno") or item.get("pd_no") or "").zfill(6)
                avg = float(item.get("pchs_avg_pric") or 0)
                holdings.append(
                    LivePosition(code=code, qty=qty, entry_price=avg, entry_date="", hold_days=0)
                )
            o2c = (raw.get("output2") or [{}])[0]

            def _m(k: str) -> float:
                v = o2c.get(k)
                return float(str(v).replace(",", "")) if v not in (None, "") else 0.0

            # [수정] dnca_tot_amt 우선 — prvs_rcdl_excc_amt 제외 (주식 평가액 선합산 필드)
            cash = (
                _m("dnca_tot_amt")
                or _m("ord_psbl_cash")
                or _m("nxdy_excc_amt")
            )
            snap = AccountSnapshot(cash=cash, positions=holdings, open_slot_count=len(holdings))
        except Exception as e:
            print(f"  API 오류: {e}")
            return

    if snap is None:
        snap = gw.get_snapshot()
    print("=== 잔고 스냅샷 ===")
    print(f"  예수금(주문가능): {snap.cash:,.0f} 원")
    print(
        f"  보유 슬롯: {snap.open_slot_count}/{snap.dynamic_max_slots} "
        f"(총자산 {snap.total_equity:,.0f}원)"
    )
    for pos in snap.positions:
        print(f"  - {pos.code} x{pos.qty} @ {pos.entry_price:,.0f}")

    ok, reason = gw.can_open_new_slot(snap)
    if ok:
        print("  슬롯 락: OK (신규 진입 가능)")
    else:
        print(f"  슬롯 락: {reason}")

    if args.order:
        print("\n=== 주문 테스트 (005930 삼성전자) ===")
        ok = gw.buy_close_price("005930", name="삼성전자", snapshot=snap)
        print(f"  결과: {ok}")


if __name__ == "__main__":
    main()
