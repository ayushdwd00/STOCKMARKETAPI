import streamlit as st
import yfinance as yf
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
import logging
import time
import os
from dotenv import load_dotenv
from groq import Groq

load_dotenv()  # loads .env locally

logging.getLogger("streamlit").setLevel(logging.ERROR)

# PAGE CONFIG
st.set_page_config(page_title="Stock Dashboard", layout="wide")

# GROQ CLIENT — works both locally (.env) and on Streamlit Cloud (secrets)
api_key = os.getenv("GROQ_API_KEY") or st.secrets.get("GROQ_API_KEY")
if not api_key:
    st.error("⚠️ GROQ_API_KEY not found. Add it to your .env file (local) or Streamlit Secrets (cloud).")
    st.stop()

client = Groq(api_key=api_key)


def get_ticker_from_llm(company_name: str) -> str:
    """Use Groq LLM to find the correct stock ticker for a company name."""
    prompt = f"""You are a stock market expert. Given a company name, return ONLY the correct stock ticker symbol.
Rules:
- For Indian stocks: use NSE format with .NS suffix (e.g. RELIANCE.NS, TCS.NS, INFY.NS, TATAMOTORS.NS)
- For US stocks: use standard ticker (e.g. AAPL, TSLA, MSFT)
- Return ONLY the ticker symbol, nothing else. No explanation, no punctuation, no extra text.

Company: {company_name}
Ticker:"""
    try:
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=20,
            temperature=0,
        )
        ticker = response.choices[0].message.content.strip().upper()
        return ticker
    except Exception as e:
        st.warning(f"⚠️ Ticker search failed: {e}")
        return None


def get_ai_summary(ticker: str, latest_close: float, period_high: float,
                   period_low: float, avg_volume: float, pct_change: float,
                   last_10_closes: list, period: str) -> str:
    """Use Groq LLM to generate a short stock summary paragraph."""
    prompt = f"""You are a financial analyst. Based on the following stock data, write a 3-4 sentence 
human-readable paragraph summarizing the stock's recent performance and trend. 
Be concise, factual, and clear. Do not use bullet points.

Stock: {ticker}
Period: {period}
Latest Close Price: {latest_close:.2f}
Period High: {period_high:.2f}
Period Low: {period_low:.2f}
Average Volume: {avg_volume:,.0f}
Price Change: {pct_change:+.2f}%
Last 10 Closing Prices: {', '.join([f'{p:.2f}' for p in last_10_closes])}

Summary:"""
    try:
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=200,
            temperature=0.5,
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        return f"Could not generate summary: {e}"


# TITLE
st.markdown("<h1 style='text-align:center;'>Live Stock Market Dashboard</h1>", unsafe_allow_html=True)
st.markdown("<p style='text-align:center; color:gray;'>Real-Time Market Intelligence</p>", unsafe_allow_html=True)

# REFRESH BUTTON
col_refresh = st.columns([4, 1, 4])[1]
with col_refresh:
    if st.button("🔄 Refresh Data"):
        st.cache_data.clear()
        st.rerun()

# SIDEBAR
st.sidebar.header("Stock Settings")

popular_stocks = {
    "Apple (AAPL)": "AAPL",
    "Tesla (TSLA)": "TSLA",
    "Microsoft (MSFT)": "MSFT",
    "Nvidia (NVDA)": "NVDA",
    "Amazon (AMZN)": "AMZN",
    "Reliance (RELIANCE.NS)": "RELIANCE.NS",
    "TCS (TCS.NS)": "TCS.NS",
    "Infosys (INFY.NS)": "INFY.NS",
    "HDFC Bank (HDFCBANK.NS)": "HDFCBANK.NS",
}

selected_stock_names = st.sidebar.multiselect(
    "Choose Popular Stocks",
    list(popular_stocks.keys()),
    default=[list(popular_stocks.keys())[0]],
)

tickers = [popular_stocks[name] for name in selected_stock_names]

# SMART TICKER SEARCH
st.sidebar.markdown("---")
st.sidebar.subheader("🔍 Search by Company Name")
company_input = st.sidebar.text_input("Type a company name (e.g. Tata Motors)")
search_clicked = st.sidebar.button("Search Ticker")

if search_clicked and company_input.strip():
    with st.spinner(f"Finding ticker for '{company_input}'..."):
        found_ticker = get_ticker_from_llm(company_input.strip())
    if found_ticker:
        st.sidebar.success(f"Found: **{found_ticker}**")
        if found_ticker not in tickers:
            tickers.append(found_ticker)
    else:
        st.sidebar.error("Could not find ticker. Try a different name.")

# CUSTOM TICKERS
st.sidebar.markdown("---")
custom_tickers = st.sidebar.text_input("Or Enter Custom Tickers (comma-separated)")
if custom_tickers:
    custom_list = [t.strip().upper() for t in custom_tickers.split(",") if t.strip()]
    tickers.extend(custom_list)

# Remove duplicates while preserving order
tickers = list(dict.fromkeys(tickers))

if not tickers:
    st.warning("Please select or search at least one stock.")
    st.stop()

period = st.sidebar.selectbox(
    "Select Time Period",
    ["1mo", "3mo", "6mo", "1y", "2y", "5y"],
)

# Supported conversion currencies
CONVERTIBLE_CURRENCIES = {"USD", "GBP", "EUR", "JPY", "HKD", "SGD"}


# DATA FETCH
@st.cache_data(ttl=300)
def get_fx_rate(from_currency: str, period_str: str) -> pd.Series:
    fx_symbol_map = {
        "USD": "INR=X",
        "GBP": "GBPINR=X",
        "EUR": "EURINR=X",
        "JPY": "JPYINR=X",
        "HKD": "HKDINR=X",
        "SGD": "SGDINR=X",
    }
    symbol = fx_symbol_map.get(from_currency)
    if not symbol:
        return None
    for attempt in range(3):
        try:
            fx = yf.Ticker(symbol)
            series = fx.history(period=period_str)["Close"]
            if not series.empty:
                return series
            time.sleep(2)
        except Exception:
            time.sleep(2)
    return None


@st.cache_data(ttl=300)
def load_data(ticker_list: list, time_period: str):
    data_dict = {}
    failed = []
    currencies = {}
    fx_cache = {}

    for symbol in ticker_list:
        for attempt in range(3):
            try:
                stock = yf.Ticker(symbol)
                df = stock.history(period=time_period)

                if df.empty:
                    if attempt < 2:
                        time.sleep(2)
                        continue
                    failed.append(symbol)
                    break

                try:
                    info = stock.fast_info
                    currency = getattr(info, "currency", None) or "INR"
                except Exception:
                    currency = "INR" if symbol.endswith(".NS") else "USD"

                currencies[symbol] = currency

                if currency != "INR" and currency in CONVERTIBLE_CURRENCIES:
                    if currency not in fx_cache:
                        fx_cache[currency] = get_fx_rate(currency, time_period)
                    fx_series = fx_cache[currency]
                    if fx_series is not None and not fx_series.empty:
                        fx_reindexed = fx_series.reindex(df.index, method="ffill").bfill()
                        for col in ["Open", "High", "Low", "Close"]:
                            df[col] = df[col] * fx_reindexed
                    else:
                        st.warning(
                            f"⚠️ Could not fetch {currency}→INR rate for **{symbol}**. "
                            "Prices shown in original currency."
                        )

                data_dict[symbol] = df
                break

            except Exception as e:
                err_str = str(e).lower()
                if "too many requests" in err_str or "rate" in err_str or "429" in err_str:
                    if attempt < 2:
                        time.sleep(3)
                    else:
                        failed.append(symbol)
                        st.warning(f"⚠️ **{symbol}** is rate-limited. Click 🔄 Refresh to retry.")
                else:
                    failed.append(symbol)
                    st.warning(f"⚠️ Failed to load **{symbol}**: {e}")
                    break

    return data_dict, failed, currencies


data, failed_tickers, stock_currencies = load_data(tickers, period)

if failed_tickers:
    st.error(
        f"Could not load data for: **{', '.join(failed_tickers)}**. "
        "Yahoo Finance may be rate-limiting. Click **🔄 Refresh Data** to retry."
    )

# DASHBOARD
if not data:
    st.error("No valid data to display. Click **🔄 Refresh Data** to retry.")
    st.stop()

elif len(data) == 1:
    ticker = list(data.keys())[0]
    df = data[ticker]
    currency_label = stock_currencies.get(ticker, "INR")
    price_unit = "₹" if currency_label == "INR" or currency_label in CONVERTIBLE_CURRENCIES else currency_label

    df["50MA"] = df["Close"].rolling(50).mean()
    df["200MA"] = df["Close"].rolling(200).mean()
    df["Daily Return"] = df["Close"].pct_change()

    st.markdown(f"<h2 style='text-align:center;'>{ticker} Market Overview</h2>", unsafe_allow_html=True)

    # Metrics
    col1, col2, col3, col4 = st.columns(4)
    latest_close = df["Close"].iloc[-1]
    prev_close = df["Close"].iloc[-2] if len(df) > 1 else latest_close
    day_change_pct = (latest_close - prev_close) / prev_close * 100

    col1.metric("Current Price", f"{price_unit} {latest_close:,.2f}", f"{day_change_pct:+.2f}%")
    col2.metric("Highest (Period)", f"{price_unit} {df['Close'].max():,.2f}")
    col3.metric("Lowest (Period)", f"{price_unit} {df['Close'].min():,.2f}")
    col4.metric("Avg Volume", f"{df['Volume'].mean():,.0f}")

    # AI SUMMARY
    st.markdown("---")
    st.subheader("📝 AI Summary")
    with st.spinner("Generating AI summary..."):
        last_10 = df["Close"].tail(10).tolist()
        summary = get_ai_summary(
            ticker=ticker,
            latest_close=latest_close,
            period_high=df["Close"].max(),
            period_low=df["Close"].min(),
            avg_volume=df["Volume"].mean(),
            pct_change=day_change_pct,
            last_10_closes=last_10,
            period=period,
        )
    st.info(summary)

    st.markdown("---")

    # Candlestick Chart
    st.subheader("Price Action")
    fig_candle = go.Figure(
        data=[
            go.Candlestick(
                x=df.index,
                open=df["Open"],
                high=df["High"],
                low=df["Low"],
                close=df["Close"],
            )
        ]
    )
    fig_candle.update_layout(
        template="plotly_dark",
        height=500,
        xaxis_rangeslider_visible=False,
        yaxis_title=f"Price ({price_unit})",
    )
    st.plotly_chart(fig_candle, use_container_width=True)

    # Moving Averages
    st.subheader("Trend Indicators (Moving Averages)")
    fig_ma = go.Figure()
    fig_ma.add_trace(go.Scatter(x=df.index, y=df["Close"], name="Close", line=dict(width=2)))
    fig_ma.add_trace(go.Scatter(x=df.index, y=df["50MA"], name="50-day MA", line=dict(dash="dash")))
    fig_ma.add_trace(go.Scatter(x=df.index, y=df["200MA"], name="200-day MA", line=dict(dash="dot")))
    fig_ma.update_layout(
        template="plotly_dark",
        yaxis_title=f"Price ({price_unit})",
        hovermode="x unified",
    )
    st.plotly_chart(fig_ma, use_container_width=True)

    # Volume
    st.subheader("Volume Analysis")
    fig_vol = px.bar(df, x=df.index, y="Volume", template="plotly_dark")
    st.plotly_chart(fig_vol, use_container_width=True)

    # Daily Returns
    st.subheader("Daily Returns (%)")
    fig_ret = px.line(df, x=df.index, y="Daily Return", template="plotly_dark")
    fig_ret.update_layout(yaxis_tickformat=".2%")
    st.plotly_chart(fig_ret, use_container_width=True)

else:
    st.markdown("<h2 style='text-align:center;'>Multi-Stock Comparison</h2>", unsafe_allow_html=True)

    # Metrics Grid
    cols = st.columns(min(len(data), 4))
    for i, (ticker, df) in enumerate(data.items()):
        col_idx = i % 4
        close_price = df["Close"].iloc[-1]
        pct_change = 0.0
        if len(df) > 1:
            pct_change = (close_price - df["Close"].iloc[-2]) / df["Close"].iloc[-2] * 100
        currency = stock_currencies.get(ticker, "INR")
        unit = "₹" if currency in {"INR"} | CONVERTIBLE_CURRENCIES else currency
        cols[col_idx].metric(ticker, f"{unit} {close_price:,.2f}", f"{pct_change:+.2f}%")

    st.markdown("---")

    # Normalized Comparison Chart
    st.subheader("Normalized Performance (%)")
    fig_comp = go.Figure()
    for ticker, df in data.items():
        if len(df) > 0:
            first_close = df["Close"].iloc[0]
            normalized = ((df["Close"] / first_close) - 1) * 100
            fig_comp.add_trace(
                go.Scatter(x=df.index, y=normalized, name=ticker, mode="lines")
            )
    fig_comp.update_layout(
        template="plotly_dark",
        yaxis_title="Return (%)",
        hovermode="x unified",
        height=500,
    )
    st.plotly_chart(fig_comp, use_container_width=True)

    # Absolute Price Comparison Chart
    st.subheader("Absolute Price Comparison (₹ / Converted)")
    fig_abs = go.Figure()
    for ticker, df in data.items():
        fig_abs.add_trace(
            go.Scatter(x=df.index, y=df["Close"], name=ticker, mode="lines")
        )
    fig_abs.update_layout(
        template="plotly_dark",
        yaxis_title="Price (INR or converted)",
        hovermode="x unified",
    )
    st.plotly_chart(fig_abs, use_container_width=True)