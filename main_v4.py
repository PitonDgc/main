import ccxt
import pandas as pd
import requests
import time
from ta.momentum import RSIIndicator, StochasticOscillator
from ta.trend import MACD, EMAIndicator, SMAIndicator
from ta.volatility import AverageTrueRange
from ta.volume import VolumeWeightedAveragePrice
from concurrent.futures import ThreadPoolExecutor

# Настройки Telegram
BOT_TOKEN = '7004223241:AAHWLchHx1-x08u95K3YMtYiO0VqFa6Rumo'
CHAT_ID = '363535031'

# Настройки анализа
SYMBOLS = [
    'BTC/USDT', 'ETH/USDT', 'XRP/USDT', 'LTC/USDT', 
    'BLUR/USDT', 'NEAR/USDT', 'WIF/USDT', 'ENA/USDT',
    'AAVE/USDT', 'SOL/USDT', 'SUI/USDT', 'LINK/USDT',
    'AVAX/USDT', 'INJ/USDT', 'DOT/USDT'
]
TIMEFRAME = '5m'  # Таймфрейм 5 минут
RSI_OVERSOLD = 30
RSI_OVERBOUGHT = 70
INTERVAL = 300  # Интервал ___ минут в секундах

exchange = ccxt.binance({
    'options': {'defaultType': 'future'},
    'enableRateLimit': True,
    'rateLimit': 3000  # Увеличиваем лимит запросов
})

# Словарь для хранения последних сигналов
last_signals = {symbol: None for symbol in SYMBOLS}

def send_telegram_message(message):
    """Отправка сообщения в Telegram"""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        'chat_id': CHAT_ID,
        'text': message,
        'parse_mode': 'Markdown'
    }
    try:
        response = requests.post(url, data=payload)
        if response.status_code != 200:
            print(f"Ошибка Telegram: {response.text}")
    except Exception as e:
        print(f"Ошибка отправки в Telegram: {e}")

def get_ohlcv(symbol):
    """Получить исторические данные"""
    try:
        ohlcv = exchange.fetch_ohlcv(symbol, TIMEFRAME, limit=200)
        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        return symbol, df
    except Exception as e:
        print(f"Ошибка данных {symbol}: {e}")
        time.sleep(5)  # Пауза при ошибке
        return symbol, None

def get_btc_dominance():
    """Получить изменение доминации биткоина (BTC.D) за последние 5 свечей"""
    try:
        ohlcv = exchange.fetch_ohlcv('BTCUSDT', TIMEFRAME, limit=6)
        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        dominance_change = (df['close'].iloc[-1] - df['close'].iloc[0]) / df['close'].iloc[0] * 100
        return dominance_change
    except Exception as e:
        print(f"Ошибка получения BTC.D: {e}")
        return 0

def analyze_data(df):
    """Анализ с главным условием MA30 и расчет шанса успеха"""
    if df is None or len(df) < 100:
        return None, 0, 0

    df['rsi'] = RSIIndicator(df['close'], window=14).rsi()
    df['macd'] = MACD(df['close']).macd()
    df['macd_signal'] = MACD(df['close']).macd_signal()
    df['stoch_k'] = StochasticOscillator(df['high'], df['low'], df['close'], window=14).stoch()
    df['stoch_d'] = df['stoch_k'].rolling(3).mean()
    df['atr'] = AverageTrueRange(df['high'], df['low'], df['close'], window=14).average_true_range()
    df['ema50'] = EMAIndicator(df['close'], window=50).ema_indicator()
    df['ema200'] = EMAIndicator(df['close'], window=200).ema_indicator()
    df['vwap'] = VolumeWeightedAveragePrice(df['high'], df['low'], df['close'], df['volume'], window=20).volume_weighted_average_price()
    df['ma30'] = SMAIndicator(df['close'], window=30).sma_indicator()
    
    last = df.iloc[-1]
    signals = []
    success_score = 0
    open_price = last['close']

    # Проверка движения: минимум 4 из 6 свечей в одну сторону (30 минут)
    last_six_candles = df['close'].iloc[-7:-1]
    candle_diffs = last_six_candles.diff().dropna()
    is_uptrend = sum(candle_diffs > 0) >= 4
    is_downtrend = sum(candle_diffs < 0) >= 4

    # Условия
    long_conditions = [
        last['ema50'] > last['ema200'],
        last['rsi'] < RSI_OVERSOLD,
        last['macd'] > last['macd_signal'],
        last['stoch_k'] < 20,
        last['close'] > last['vwap'],
        last['volume'] > df['volume'].rolling(20).mean().iloc[-1]
    ]
    short_conditions = [
        last['ema50'] < last['ema200'],
        last['rsi'] > RSI_OVERBOUGHT,
        last['macd'] < last['macd_signal'],
        last['stoch_k'] > 80,
        last['close'] < last['vwap'],
        last['volume'] > df['volume'].rolling(20).mean().iloc[-1]
    ]

    avg_volume = df['volume'].rolling(20).mean().iloc[-1]
    volume_ratio = last['volume'] / avg_volume if avg_volume > 0 else 1
    btc_dominance_change = get_btc_dominance()

    # Лонг: MA30 обязательна, плюс 5/6 остальных условий
    if last['close'] > last['ma30'] and sum(long_conditions) >= 5:
        signal = f"🟢 ЛОНГ (Условий: {sum(long_conditions) + 1}/7)"
        signals.append(signal)
        success_score = 50  # Базовый шанс за 6/7
        if sum(long_conditions) == 6:  # Все 6 условий
            success_score += 20
        else:  # 5 условий
            success_score += 10
        if volume_ratio > 3:
            success_score += 15
        elif volume_ratio > 2:
            success_score += 10
        elif volume_ratio > 1.5:
            success_score += 5
        if is_uptrend:
            success_score += 10
        if btc_dominance_change < 0:
            success_score += 5
        elif btc_dominance_change > 0:
            success_score -= 5
    
    # Шорт: MA30 обязательна, плюс 5/6 остальных условий
    if last['close'] < last['ma30'] and sum(short_conditions) >= 5:
        signal = f"🔴 ШОРТ (Условий: {sum(short_conditions) + 1}/7)"
        signals.append(signal)
        success_score = 50  # Базовый шанс за 6/7
        if sum(short_conditions) == 6:  # Все 6 условий
            success_score += 20
        else:  # 5 условий
            success_score += 10
        if volume_ratio > 3:
            success_score += 15
        elif volume_ratio > 2:
            success_score += 10
        elif volume_ratio > 1.5:
            success_score += 5
        if is_downtrend:
            success_score += 10
        if btc_dominance_change > 0:
            success_score += 5
        elif btc_dominance_change < 0:
            success_score -= 5
    
    return signals, min(success_score, 100), open_price

def process_symbol(symbol):
    """Обработка одной пары"""
    symbol, df = get_ohlcv(symbol)
    signals, success_score, open_price = analyze_data(df)
    return symbol, df, signals, success_score, open_price

def main():
    while True:
        try:
            print(f"📊 Проверка {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M:%S')}")
            
            with ThreadPoolExecutor(max_workers=15) as executor:
                results = list(executor.map(process_symbol, SYMBOLS))
            
            all_signals = []
            for symbol, df, signals, success_score, open_price in results:
                if df is not None:
                    print(f"\n{symbol}:")
                    print(f"  Цена: {df['close'].iloc[-1]:.2f}")
                    print(f"  EMA50/200: {df['ema50'].iloc[-1]:.2f}/{df['ema200'].iloc[-1]:.2f}")
                    print(f"  MA30: {df['ma30'].iloc[-1]:.2f}")
                    print(f"  RSI: {df['rsi'].iloc[-1]:.1f} | MACD: {df['macd'].iloc[-1]:.2f}")
                    print(f"  Объем: {df['volume'].iloc[-1]:.0f} (Средний: {df['volume'].rolling(20).mean().iloc[-1]:.0f})")
                    if signals:
                        for signal in signals:
                            print(f"  → {signal} (Шанс успеха: {success_score:.0f}%)")
                            all_signals.append((symbol, signal, success_score, open_price))
                    else:
                        print("  Нет сигналов")
                else:
                    print(f"\n{symbol}: Нет данных")

            if all_signals:
                best_symbol, best_signal, best_score, best_open_price = max(all_signals, key=lambda x: x[2])
                if last_signals[best_symbol] != best_signal:
                    send_telegram_message(
                        f"*{best_symbol}* → {best_signal}\n"
                        f"Цена открытия: {best_open_price:.2f}\n"
                        f"Шанс успеха: {best_score:.0f}%"
                    )
                    last_signals[best_symbol] = best_signal
                    print(f"\nЛучший сигнал: {best_symbol} → {best_signal} (Шанс успеха: {best_score:.0f}%)")
            
            print(f"Ожидание следующей проверки ({INTERVAL // 60} мин)...")
            time.sleep(INTERVAL)
        
        except Exception as e:
            print(f"Ошибка в главном цикле: {e}")
            time.sleep(60)  # Пауза 1 минута при ошибке

if __name__ == "__main__":
    main()