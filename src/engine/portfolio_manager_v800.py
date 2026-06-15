"""
v8.0.0 ORB 백테스트 엔진 — 분봉 인프라 전제 스켈레톤.

일봉 relay(v7)와 다른 데이터 계층(1분/5분봉)이 필요하므로
분봉 파이프라인 구축 전까지는 run_v8_00_orb_research.py에서 설계 검증만 수행한다.
"""
from __future__ import annotations

from dataclasses import dataclass

from src.engine.orb_strategy import OrbConfig


@dataclass
class Alpha800Config(OrbConfig):
    """v8.0.0 ORB 파라미터 — OrbConfig 상속."""


class PortfolioManagerV800:
    """
    분봉 ORB 시뮬레이터 (TODO).

    Required inputs (future):
      - minute_bars_by_code: dict[str, pd.DataFrame]  # 당일 1분/5분봉
      - prior_day_snapshot: pd.DataFrame              # D-1 거래대금·종가

    Execution model:
      - Entry: 09:05~09:30 Opening High 돌파 체결 (look-ahead 금지)
      - Exit: +5% / -2% / 14:50 same-day close
    """

    def __init__(self, *, alpha: Alpha800Config | None = None):
        self.alpha = alpha or Alpha800Config()

    def run(self):
        raise NotImplementedError(
            "v8.0 ORB 백테스트는 분봉 데이터 파이프라인 구축 후 활성화됩니다. "
            "현재는 src/engine/orb_strategy.py 유닛 테스트 및 live_orb 실전 루프를 사용하세요."
        )
