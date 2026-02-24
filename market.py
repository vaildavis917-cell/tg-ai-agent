"""
Рыночные данные — полностью асинхронный модуль на aiohttp.
Yahoo Finance API с fallback на CoinGecko и ExchangeRate.
Кэширование и форматирование для промпта.
"""
import os
import json
import time
import logging
import xml.etree.ElementTree as ET
import aiohttp
from config import MARKET_CACHE_FILE, MARKET_UPDATE_INTERVAL

logger = logging.getLogger("tg_agent")

market_cache = {"prices": {}, "news": [], "last_update": 0}

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

# ============ YAHOO FINANCE (основной источник) ============
YAHOO_SYMBOLS = {
    'GC=F': 'Золото', 'SI=F': 'Серебро', 'CL=F': 'Нефть WTI',
    'EURUSD=X': 'EUR/USD', 'GBPUSD=X': 'GBP/USD', 'USDJPY=X': 'USD/JPY',
    '^GSPC': 'S&P 500', '^IXIC': 'NASDAQ',
    'BTC-USD': 'BTC',
}


async def fetch_yahoo_finance() -> dict:
    """Yahoo Finance: форекс, сырьё, индексы, крипта (async)"""
    prices = {}
    timeout = aiohttp.ClientTimeout(total=15)
    try:
        async with aiohttp.ClientSession(timeout=timeout, headers=HEADERS) as session:
            for sym, name in YAHOO_SYMBOLS.items():
                try:
                    url = f'https://query1.finance.yahoo.com/v8/finance/chart/{sym}?interval=1d&range=2d'
                    async with session.get(url) as resp:
                        if resp.status != 200:
                            logger.warning(f"Yahoo Finance {sym}: HTTP {resp.status}")
                            continue
                        data = await resp.json()
                        meta = data['chart']['result'][0]['meta']
                        price = meta.get('regularMarketPrice', 0)
                        prev = meta.get('chartPreviousClose', 0)
                        change = round((price - prev) / prev * 100, 2) if prev > 0 else 0
                        prices[name] = {'price': price, 'change_24h': change}
                except Exception as e:
                    logger.error(f"Ошибка Yahoo Finance {sym}: {e}")
    except Exception as e:
        logger.error(f"Ошибка создания aiohttp сессии (Yahoo): {e}")
    return prices


# ============ FALLBACK: CoinGecko (для крипты) ============
async def fetch_coingecko_fallback() -> dict:
    """CoinGecko API — fallback для крипто-данных (async)"""
    prices = {}
    timeout = aiohttp.ClientTimeout(total=10)
    try:
        async with aiohttp.ClientSession(timeout=timeout, headers=HEADERS) as session:
            url = "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin,ethereum,solana&vs_currencies=usd&include_24hr_change=true"
            async with session.get(url) as resp:
                if resp.status != 200:
                    logger.warning(f"CoinGecko: HTTP {resp.status}")
                    return prices
                data = await resp.json()
                mapping = {"bitcoin": "BTC", "ethereum": "ETH", "solana": "SOL"}
                for coin_id, name in mapping.items():
                    if coin_id in data:
                        price = data[coin_id].get("usd", 0)
                        change = round(data[coin_id].get("usd_24h_change", 0), 2)
                        prices[name] = {"price": price, "change_24h": change}
                logger.info(f"CoinGecko fallback: {len(prices)} монет загружено")
    except Exception as e:
        logger.error(f"Ошибка CoinGecko fallback: {e}")
    return prices


# ============ FALLBACK: ExchangeRate для форекса ============
async def fetch_exchangerate_fallback() -> dict:
    """ExchangeRate API — fallback для форекса (async)"""
    prices = {}
    timeout = aiohttp.ClientTimeout(total=10)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            url = "https://open.er-api.com/v6/latest/USD"
            async with session.get(url) as resp:
                if resp.status != 200:
                    logger.warning(f"ExchangeRate: HTTP {resp.status}")
                    return prices
                data = await resp.json()
                rates = data.get("rates", {})
                if "EUR" in rates:
                    prices["EUR/USD"] = {"price": round(1 / rates["EUR"], 4), "change_24h": 0}
                if "GBP" in rates:
                    prices["GBP/USD"] = {"price": round(1 / rates["GBP"], 4), "change_24h": 0}
                if "JPY" in rates:
                    prices["USD/JPY"] = {"price": round(rates["JPY"], 2), "change_24h": 0}
                logger.info(f"ExchangeRate fallback: {len(prices)} пар загружено")
    except Exception as e:
        logger.error(f"Ошибка ExchangeRate fallback: {e}")
    return prices


# ============ НОВОСТИ (async) ============
async def fetch_news() -> list:
    """Парсинг новостей с разных рынков (async)"""
    news = []
    feeds = [
        ('https://www.investing.com/rss/news.rss', 'Рынки'),
        ('https://cointelegraph.com/rss', 'Крипто'),
    ]
    timeout = aiohttp.ClientTimeout(total=10)
    try:
        async with aiohttp.ClientSession(timeout=timeout, headers=HEADERS) as session:
            for feed_url, source in feeds:
                try:
                    async with session.get(feed_url) as resp:
                        if resp.status != 200:
                            continue
                        content = await resp.read()
                        root = ET.fromstring(content)
                        items = root.findall('.//item')[:3]
                        for item in items:
                            title = item.find('title')
                            if title is not None and title.text:
                                news.append(title.text.strip())
                except Exception as e:
                    logger.error(f"Ошибка парсинга {source}: {e}")
    except Exception as e:
        logger.error(f"Ошибка создания aiohttp сессии (news): {e}")
    return news[:5]


# ============ ОБНОВЛЕНИЕ КЭША (async) ============
async def update_market_cache():
    """Обновляет кэш рыночных данных с fallback (async)"""
    now = time.time()
    if now - market_cache["last_update"] < MARKET_UPDATE_INTERVAL:
        return

    # Основной источник — Yahoo Finance
    prices = await fetch_yahoo_finance()

    # Fallback: если Yahoo не вернул крипту — берём из CoinGecko
    if "BTC" not in prices:
        logger.warning("Yahoo Finance не вернул BTC, пробуем CoinGecko fallback...")
        crypto_fallback = await fetch_coingecko_fallback()
        prices.update(crypto_fallback)

    # Fallback: если Yahoo не вернул форекс — берём из ExchangeRate
    if "EUR/USD" not in prices:
        logger.warning("Yahoo Finance не вернул форекс, пробуем ExchangeRate fallback...")
        forex_fallback = await fetch_exchangerate_fallback()
        prices.update(forex_fallback)

    news = await fetch_news()

    if prices:
        market_cache["prices"] = prices
    if news:
        market_cache["news"] = news
    market_cache["last_update"] = now

    # Сохраняем на диск
    try:
        with open(MARKET_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(market_cache, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

    price_str = ', '.join(f"{k}=${v['price']}({v['change_24h']:+}%)" for k, v in prices.items())
    logger.info(f"Рынок обновлён: {price_str} | Новостей: {len(news)}")


def load_market_cache():
    """Загружает кэш рынка с диска (синхронно, вызывается при старте)"""
    global market_cache
    try:
        if os.path.exists(MARKET_CACHE_FILE):
            with open(MARKET_CACHE_FILE, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            market_cache.clear()
            market_cache.update(loaded)
            logger.info("Загружен кэш рынка")
    except Exception as e:
        logger.error(f"Ошибка загрузки кэша рынка: {e}")


async def get_market_context() -> str:
    """Формирует контекст рыночных данных для промпта Claude (async)"""
    await update_market_cache()
    parts = []
    prices = market_cache.get("prices", {})
    if prices:
        def fmt(name, data):
            change = data['change_24h']
            direction = 'вырос' if change > 0 else 'упал'
            price = data['price']
            if price > 100:
                return f"{name}: ${price:,.0f} ({direction} на {abs(change)}%)"
            else:
                return f"{name}: {price:.4f} ({direction} на {abs(change)}%)"

        commodities = {k: v for k, v in prices.items() if k in ['Золото', 'Серебро', 'Нефть WTI']}
        forex = {k: v for k, v in prices.items() if '/' in k}
        indices = {k: v for k, v in prices.items() if k in ['S&P 500', 'NASDAQ']}
        crypto = {k: v for k, v in prices.items() if k in ['BTC', 'ETH', 'SOL']}

        lines = []
        if commodities:
            lines.append('Сырьё: ' + ', '.join(fmt(k, v) for k, v in commodities.items()))
        if forex:
            lines.append('Форекс: ' + ', '.join(fmt(k, v) for k, v in forex.items()))
        if indices:
            lines.append('Индексы: ' + ', '.join(fmt(k, v) for k, v in indices.items()))
        if crypto:
            lines.append('Крипто: ' + ', '.join(fmt(k, v) for k, v in crypto.items()))
        parts.append('Рынки сейчас:\n' + '\n'.join(lines))

    news = market_cache.get("news", [])
    if news:
        parts.append("Последние новости рынков:\n" + "\n".join(f"- {n}" for n in news[:3]))

    if not parts:
        return ""

    return ("\n\n[АКТУАЛЬНЫЕ ДАННЫЕ РЫНКОВ]\n" + "\n".join(parts) +
            "\nИспользуй эти данные естественно в разговоре. Упомяни 1-2 факта как аргумент. "
            "Мы торгуем всеми активами -- форекс, сырьё, индексы, крипту. НЕ говори что это только крипта.")
