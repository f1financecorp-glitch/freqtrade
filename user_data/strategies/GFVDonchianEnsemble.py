"""
GFVDonchianEnsemble — F1 Finance Corp / Gale Force Vector

Daily BTC trend-following. Four Donchian breakout sub-models (20/55/100/200 days)
are averaged into one target weight, then scaled down when BTC volatility is high.

Rules (fixed up front — NOT optimized on this data):
  Sub-model N, long:   enter when close > highest high of prior N days
                       exit  when close < lowest low of prior N/2 days
  Sub-model N, short:  mirror image (only used when ENABLE_SHORT = True, futures mode)
  Ensemble score:      average of the 4 sub-models (long 0..1, net -1..+1 with shorts)
  Vol sizing:          target_weight = score * min(1, TARGET_VOL / realized 30d vol)
  Execution:           signal on daily close, filled next candle open (freqtrade default)
  Rebalance:           only when target drifts > REBALANCE_BAND of the wallet

Backtest finding (Sept 2026): the SHORT side lost money in every period tested
(2016-2020, 2021-mid23 incl. the 2022 bear, mid23-2026). Shorts stay OFF by default.

Paper-trade (dry_run) before any live money. Not financial advice.
"""

from datetime import datetime

import numpy as np
from pandas import DataFrame

from freqtrade.persistence import Trade
from freqtrade.strategy import IStrategy


LOOKBACKS = (20, 55, 100, 200)
TARGET_VOL = 0.40          # annualized volatility target (chosen up front, not tuned)
VOL_WINDOW = 30            # days of returns used for realized vol
REBALANCE_BAND = 0.10      # rebalance only when target differs by > 10% of wallet
ENABLE_SHORT = False       # shorts tested negative — keep off unless re-validated


def _donchian_state(close, high_prev, low_prev, enter_above: bool) -> np.ndarray:
    """Stateful breakout: 1 while in position, 0 when flat.
    enter_above=True  -> long model  (enter close>high_prev, exit close<low_prev)
    enter_above=False -> short model (enter close<low_prev,  exit close>high_prev)"""
    state = np.zeros(len(close))
    pos = 0
    for i in range(len(close)):
        hp, lp, c = high_prev[i], low_prev[i], close[i]
        if np.isnan(hp) or np.isnan(lp):
            state[i] = 0
            continue
        if enter_above:
            if pos == 0 and c > hp:
                pos = 1
            elif pos == 1 and c < lp:
                pos = 0
        else:
            if pos == 0 and c < lp:
                pos = 1
            elif pos == 1 and c > hp:
                pos = 0
        state[i] = pos
    return state


class GFVDonchianEnsemble(IStrategy):
    INTERFACE_VERSION = 3

    timeframe = "1d"
    can_short = ENABLE_SHORT
    startup_candle_count = 210

    # Exits are signal-driven; ROI / stoploss effectively disabled (the N/2 channel is the stop)
    minimal_roi = {"0": 100}
    stoploss = -0.99
    trailing_stop = False
    use_exit_signal = True
    exit_profit_only = False

    position_adjustment_enable = True
    max_entry_position_adjustment = -1

    process_only_new_candles = True

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        close = dataframe["close"].values
        long_states, short_states = [], []
        for n in LOOKBACKS:
            hi_n = dataframe["high"].rolling(n).max().shift(1).values
            lo_n = dataframe["low"].rolling(n).min().shift(1).values
            hi_x = dataframe["high"].rolling(n // 2).max().shift(1).values
            lo_x = dataframe["low"].rolling(n // 2).min().shift(1).values
            # long: enter above N-day high, exit below N/2-day low
            long_states.append(_donchian_state(close, hi_n, lo_x, enter_above=True))
            # short: enter below N-day low, exit above N/2-day high
            short_states.append(_donchian_state(close, hi_x, lo_n, enter_above=False))

        dataframe["long_score"] = np.mean(long_states, axis=0)
        dataframe["short_score"] = np.mean(short_states, axis=0)

        ret = dataframe["close"].pct_change()
        realized_vol = ret.rolling(VOL_WINDOW).std() * np.sqrt(365)
        dataframe["vol_scalar"] = (TARGET_VOL / realized_vol).clip(upper=1.0).fillna(0)

        net = dataframe["long_score"] - (dataframe["short_score"] if ENABLE_SHORT else 0)
        dataframe["target_weight"] = (net * dataframe["vol_scalar"]).clip(-1, 1)
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[dataframe["target_weight"] > 0, ["enter_long", "enter_tag"]] = (1, "donchian_long")
        if ENABLE_SHORT:
            dataframe.loc[dataframe["target_weight"] < 0, ["enter_short", "enter_tag"]] = (
                1,
                "donchian_short",
            )
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[dataframe["target_weight"] <= 0, "exit_long"] = 1
        if ENABLE_SHORT:
            dataframe.loc[dataframe["target_weight"] >= 0, "exit_short"] = 1
        return dataframe

    # ---- sizing -----------------------------------------------------------------
    def _target_weight(self, pair: str) -> float:
        df, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if df is None or df.empty:
            return 0.0
        return float(df["target_weight"].iloc[-1])

    def custom_stake_amount(
        self,
        pair: str,
        current_time: datetime,
        current_rate: float,
        proposed_stake: float,
        min_stake: float | None,
        max_stake: float,
        leverage: float,
        entry_tag: str | None,
        side: str,
        **kwargs,
    ) -> float:
        wallet = self.wallets.get_total_stake_amount()
        stake = abs(self._target_weight(pair)) * wallet
        return max(min(stake, max_stake), min_stake or 0)

    def adjust_trade_position(
        self,
        trade: Trade,
        current_time: datetime,
        current_rate: float,
        current_profit: float,
        min_stake: float | None,
        max_stake: float,
        current_entry_rate: float,
        current_exit_rate: float,
        current_entry_profit: float,
        current_exit_profit: float,
        **kwargs,
    ) -> float | tuple[float | None, str | None] | None:
        if trade.has_open_orders:
            return None
        wallet = self.wallets.get_total_stake_amount()
        target_stake = abs(self._target_weight(trade.pair)) * wallet
        current_value = trade.amount * current_rate / trade.leverage
        diff = target_stake - current_value
        if abs(diff) < REBALANCE_BAND * wallet:
            return None
        if diff > 0:
            return min(diff, max_stake), "rebalance_up"
        # reduce position (negative stake = partial exit); never below min stake
        reduce = min(-diff, current_value - (min_stake or 0))
        if reduce <= 0:
            return None
        return -reduce, "rebalance_down"

    def leverage(self, pair: str, current_time: datetime, current_rate: float,
                 proposed_leverage: float, max_leverage: float, entry_tag: str | None,
                 side: str, **kwargs) -> float:
        return 1.0  # no leverage — sizing already controls risk
