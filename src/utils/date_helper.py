"""
KRX 근거 일중 시각 및 영업일(주말) 등을 표준화해 오버나이트 스캔의 '기준일(t0)·전일(-1)·전전일(-2)'를
CLI·GUI 공통 단일 규격으로 제공한다.

KRX 현물 장중 시간은 변경될 수 있으므로, 공휴일 캘린더는 도입하지 않았을 때에는
pandas BDay (월~금)와 동작 시각 규칙의 조합으로만 정의한다(KRX 교체 휴장일은 차기 확장 가능).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import pandas as pd
from pandas.tseries.offsets import BDay

KST = ZoneInfo("Asia/Seoul")

# 근거: KRX 정규장 (현물 공통 근사)
KRX_REGULAR_SESSION_OPEN_TIME = time(9, 0)
KRX_REGULAR_SESSION_LAST_MINUTE_CLOSE = time(15, 30)


@dataclass(frozen=True, slots=True)
class OvernightScanAnchor:
    """사용자가 고른 종료일(달력)·실제 평가 봉 t0 및 전 거래영업일 두 개."""

    requested_calendar_date: date
    anchor_date: date
    prev_business_dates: tuple[date, date]
    anchor_policy_reason: str

    @property
    def prev_1(self) -> date:
        return self.prev_business_dates[0]

    @property
    def prev_2(self) -> date:
        return self.prev_business_dates[1]


def resolve_overnight_scan_anchor(
    requested_end: str | date,
    *,
    reference_now: datetime | None = None,
) -> OvernightScanAnchor:
    """
    종료일 문자열 또는 date를 받아 오버나이트 벌크/CLI가 공통으로 쓸 (t0, prev1, prev2)를 계산한다.

    정책(아시아/서울 기준 동일 즉시):
    - 과거 또는 '오늘' 이전의 달력일: 그 날을 t0 로 사용(히스토리 백테스트).
    - '오늘'이 평일:
        • 09:00 전: 미완결 일간 봉으로 보고 t0 는 직전 영업일
        • 09:00~15:30(포함): 일봉 OHLC 검증 규격과 동기화 위해 t0 도 직전 영업일(장중이라도 미집계와 혼선 방지)
        • 15:30 초과: 같은 달력일을 t0 로 간주(종가 포함 일봉 사용 가정).
    - '오늘'이 주토일: 금요일(or 직전 영업일 방향)까지 한 칸 빼는 BDay(1) 패턴 사용.
    - 요청 일이 현재 서울 달력을 초과하면 오늘로 클램프 후 위 규칙 적용(future_clamped).
    """
    if isinstance(requested_end, date):
        req0 = requested_end
    else:
        req0 = pd.Timestamp(str(requested_end).strip()[:10]).date()

    now = reference_now or datetime.now(KST)
    if now.tzinfo is None:
        now = now.replace(tzinfo=KST)
    else:
        now = now.astimezone(KST)

    cur = now.date()
    clock = now.time()

    reason = "historical_remote"
    if req0 > cur:
        req = cur
        reason = "future_clamped_then_session_rule"
    else:
        req = req0

    if req < cur:
        anchor = req
        reason = "historical_remote"
    else:
        assert req == cur
        wd = req.weekday()
        if wd >= 5:
            anchor = (pd.Timestamp(req) - BDay(1)).date()
            reason = "weekend_use_prior_business_close"
        elif clock < KRX_REGULAR_SESSION_OPEN_TIME:
            anchor = (pd.Timestamp(req) - BDay(1)).date()
            reason = "pre_open_use_prior_session"
        elif clock <= KRX_REGULAR_SESSION_LAST_MINUTE_CLOSE:
            anchor = (pd.Timestamp(req) - BDay(1)).date()
            reason = "intraday_daily_bar_not_finalized_use_prior"
        else:
            anchor = req
            reason = "post_regular_close_same_calendar_day"

    ts_a = pd.Timestamp(anchor)
    p1 = (ts_a - BDay(1)).date()
    p2 = (ts_a - BDay(2)).date()
    return OvernightScanAnchor(req0, anchor, (p1, p2), reason)


def resolve_chart_period_end(requested_end: str | date) -> date:
    """
    차트 조회 종료일: 사용자가 고른 달력일을 그대로 사용(미래만 오늘로 클램프).

    오버나이트 스캔의 `resolve_overnight_scan_anchor` 와 달리 장중에도
    종료일을 전일로 당기지 않는다 — 차트는 '선택한 기간의 봉'을 보여 주기 위함.
    """
    if isinstance(requested_end, date):
        req0 = requested_end
    else:
        req0 = pd.Timestamp(str(requested_end).strip()[:10]).date()
    cur = datetime.now(KST).date()
    if req0 > cur:
        return cur
    return req0
