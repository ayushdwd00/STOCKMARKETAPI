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

load_dotenv()
logging.getLogger("streamlit").setLevel(logging.ERROR)

# ── PAGE CONFIG ────────────────────────────────────────────────────────────────
st.set_page_config(page_title="Stock Dashboard", layout="wide")

# ── GROQ CLIENT ────────────────────────────────────────────────────────────────
api_key = os.getenv("GROQ_API_KEY") or st.secrets.get("GROQ_API_KEY")
if not api_key:
    st.error("⚠️ GROQ_API_KEY not found. Add it to your .env file or Streamlit Secrets.")
    st.stop()

client = Groq(api_key=api_key)

# ── SESSION STATE ──────────────────────────────────────────────────────────────
if "primary_ticker" not in st.session_state:
    st.session_state.primary_ticker = None       # first / main stock
if "comparison_tickers" not in st.session_state:
    st.session_state.comparison_tickers = []     # additional stocks for comparison
if "llm_cache" not in st.session_state:
    st.session_state.llm_cache = {}              # company name → ticker cache

# ── POPULAR STOCKS ─────────────────────────────────────────────────────────────
POPULAR_STOCKS = {
    "AAPL  — Apple":            "AAPL",
    "TSLA  — Tesla":            "TSLA",
    "MSFT  — Microsoft":        "MSFT",
    "NVDA  — Nvidia":           "NVDA",
    "AMZN  — Amazon":           "AMZN",
    "GOOGL — Alphabet":         "GOOGL",
    "META  — Meta":             "META",
    "RELIANCE.NS — Reliance":   "RELIANCE.NS",
    "TCS.NS — TCS":             "TCS.NS",
    "INFY.NS — Infosys":        "INFY.NS",
    "HDFCBANK.NS — HDFC Bank":  "HDFCBANK.NS",
    "WIPRO.NS — Wipro":         "WIPRO.NS",
}

CONVERTIBLE_CURRENCIES = {"USD", "GBP", "EUR", "JPY", "HKD", "SGD"}

# ── LLM HELPERS ────────────────────────────────────────────────────────────────
def get_ticker_from_llm(company_name: str) -> str:
    if company_name in st.session_state.llm_cache:
        return st.session_state.llm_cache[company_name]
    prompt = f"""You are a stock market expert. Given a company name, return ONLY the correct stock ticker symbol.
Rules:
- For Indian stocks: use NSE format with .NS suffix (e.g. RELIANCE.NS, TCS.NS, INFY.NS)
- For US stocks: use standard ticker (e.g. AAPL, TSLA, MSFT)
- Return ONLY the ticker symbol, nothing else.

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
        st.session_state.llm_cache[company_name] = ticker
        return ticker
    except Exception as e:
        st.warning(f"⚠️ Ticker lookup failed: {e}")
        return None


def get_ai_summary(ticker, latest_close, period_high, period_low,
                   avg_volume, pct_change, last_10_closes, period) -> str:
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


# ── DATA FETCHING ──────────────────────────────────────────────────────────────
@st.cache_data(ttl=300)
def get_fx_rate(from_currency: str, period_str: str) -> pd.Series:
    fx_map = {
        "USD": "INR=X", "GBP": "GBPINR=X", "EUR": "EURINR=X",
        "JPY": "JPYINR=X", "HKD": "HKDINR=X", "SGD": "SGDINR=X",
    }
    symbol = fx_map.get(from_currency)
    if not symbol:
        return None
    for _ in range(3):
        try:
            series = yf.Ticker(symbol).history(period=period_str)["Close"]
            if not series.empty:
                return series
            time.sleep(2)
        except Exception:
            time.sleep(2)
    return None


@st.cache_data(ttl=300)
def load_data(ticker_list: tuple, time_period: str):
    data_dict, failed, currencies = {}, [], {}
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
                    currency = getattr(stock.fast_info, "currency", None) or "INR"
                except Exception:
                    currency = "INR" if symbol.endswith(".NS") else "USD"
                currencies[symbol] = currency
                if currency != "INR" and currency in CONVERTIBLE_CURRENCIES:
                    if currency not in fx_cache:
                        fx_cache[currency] = get_fx_rate(currency, time_period)
                    fx = fx_cache[currency]
                    if fx is not None and not fx.empty:
                        fx_r = fx.reindex(df.index, method="ffill").bfill()
                        for col in ["Open", "High", "Low", "Close"]:
                            df[col] = df[col] * fx_r
                data_dict[symbol] = df
                break
            except Exception as e:
                err = str(e).lower()
                if any(x in err for x in ["too many requests", "rate", "429"]):
                    if attempt < 2:
                        time.sleep(3)
                    else:
                        failed.append(symbol)
                        st.warning(f"⚠️ **{symbol}** rate-limited. Click 🔄 Refresh.")
                else:
                    failed.append(symbol)
                    st.warning(f"⚠️ Failed to load **{symbol}**: {e}")
                    break
    return data_dict, failed, currencies


# ── SIDEBAR ────────────────────────────────────────────────────────────────────
st.sidebar.markdown("## 📈 Stock Settings")
st.sidebar.markdown("---")

# ── 1) UNIFIED SEARCH BAR ─────────────────────────────────────────────────────
st.sidebar.markdown("#### Search Stock")

popular_labels = list(POPULAR_STOCKS.keys())
popular_tickers = list(POPULAR_STOCKS.values())

search_input = st.sidebar.text_input(
    "Type a ticker or company name",
    placeholder="e.g. AAPL, Reliance, TSLA…",
    key="search_input",
)

# Suggestions dropdown from popular list
suggestion = st.sidebar.selectbox(
    "Or pick from popular stocks",
    options=[""] + popular_labels,
    index=0,
    key="suggestion_select",
    format_func=lambda x: "— choose a popular stock —" if x == "" else x,
)

if st.sidebar.button("🔍 Search / Add Stock", use_container_width=True):
    raw = search_input.strip() or (POPULAR_STOCKS.get(suggestion, "") if suggestion else "")
    if raw:
        # Check if it looks like a ticker (short, no spaces) or a company name
        if len(raw.split()) == 1 and raw.replace(".", "").isalpha():
            resolved = raw.upper()
        else:
            with st.spinner(f"Looking up '{raw}'…"):
                resolved = get_ticker_from_llm(raw)

        if resolved:
            if st.session_state.primary_ticker is None:
                st.session_state.primary_ticker = resolved
                st.sidebar.success(f"✅ Primary: **{resolved}**")
            elif resolved not in st.session_state.comparison_tickers and resolved != st.session_state.primary_ticker:
                st.session_state.comparison_tickers.append(resolved)
                st.sidebar.success(f"✅ Added to comparison: **{resolved}**")
            else:
                st.sidebar.info(f"**{resolved}** is already added.")
        else:
            st.sidebar.error("Could not resolve ticker. Try the exact ticker symbol.")
    else:
        st.sidebar.warning("Please type a ticker or select from the list.")

st.sidebar.markdown("---")

# ── 2) TIME FRAME ──────────────────────────────────────────────────────────────
st.sidebar.markdown("#### Time Frame")
period = st.sidebar.radio(
    "Select period",
    options=["1mo", "3mo", "6mo", "1y", "2y", "5y"],
    index=3,
    horizontal=True,
    label_visibility="collapsed",
)

st.sidebar.markdown("---")

# ── 3) COMPARISON PANEL (active only after first stock added) ──────────────────
st.sidebar.markdown("#### Comparison")

if st.session_state.primary_ticker is None:
    st.sidebar.caption("🔒 Search a stock first to enable comparison.")
else:
    primary = st.session_state.primary_ticker
    st.sidebar.markdown(f"**Primary:** `{primary}`")

    if st.session_state.comparison_tickers:
        st.sidebar.markdown("**Comparing with:**")
        for t in st.session_state.comparison_tickers.copy():
            col1, col2 = st.sidebar.columns([3, 1])
            col1.markdown(f"`{t}`")
            if col2.button("✕", key=f"remove_{t}"):
                st.session_state.comparison_tickers.remove(t)
                st.rerun()
    else:
        st.sidebar.caption("Search another stock above to compare.")

    if st.sidebar.button("🗑️ Clear All", use_container_width=True):
        st.session_state.primary_ticker = None
        st.session_state.comparison_tickers = []
        st.rerun()

# ── REFRESH ────────────────────────────────────────────────────────────────────
st.sidebar.markdown("---")
if st.sidebar.button("🔄 Refresh Data", use_container_width=True):
    st.cache_data.clear()
    st.rerun()

# ── BUILD TICKER LIST ──────────────────────────────────────────────────────────
if st.session_state.primary_ticker is None:
    st.markdown("<h1 style='text-align:center;'>📊 Live Stock Market Dashboard</h1>", unsafe_allow_html=True)
    st.markdown("<p style='text-align:center;color:gray;'>Search a stock in the sidebar to get started.</p>", unsafe_allow_html=True)
    st.stop()

all_tickers = [st.session_state.primary_ticker] + st.session_state.comparison_tickers

# ── FETCH DATA ─────────────────────────────────────────────────────────────────
data, failed_tickers, stock_currencies = load_data(tuple(all_tickers), period)

if failed_tickers:
    st.error(f"Could not load: **{', '.join(failed_tickers)}**. Click 🔄 Refresh to retry.")

if not data:
    st.error("No valid data. Click 🔄 Refresh.")
    st.stop()

# ── PAGE HEADER ────────────────────────────────────────────────────────────────
st.markdown("<h1 style='text-align:center;'>📊 Live Stock Market Dashboard</h1>", unsafe_allow_html=True)
st.markdown("<p style='text-align:center;color:gray;'>Real-Time Market Intelligence</p>", unsafe_allow_html=True)

# ══════════════════════════════════════════════════════════════════════════════
# SINGLE STOCK VIEW
# ══════════════════════════════════════════════════════════════════════════════
if len(data) == 1:
    ticker = list(data.keys())[0]
    df = data[ticker]
    currency_label = stock_currencies.get(ticker, "INR")
    price_unit = "₹" if currency_label in {"INR"} | CONVERTIBLE_CURRENCIES else currency_label

    df["50MA"]        = df["Close"].rolling(50).mean()
    df["200MA"]       = df["Close"].rolling(200).mean()
    df["Daily Return"] = df["Close"].pct_change()

    st.markdown(f"<h2 style='text-align:center;'>{ticker} — Market Overview</h2>", unsafe_allow_html=True)

    latest_close = df["Close"].iloc[-1]
    prev_close   = df["Close"].iloc[-2] if len(df) > 1 else latest_close
    day_change   = (latest_close - prev_close) / prev_close * 100

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Current Price",   f"{price_unit} {latest_close:,.2f}", f"{day_change:+.2f}%")
    c2.metric("Period High",     f"{price_unit} {df['Close'].max():,.2f}")
    c3.metric("Period Low",      f"{price_unit} {df['Close'].min():,.2f}")
    c4.metric("Avg Volume",      f"{df['Volume'].mean():,.0f}")

    # AI Summary
    st.markdown("---")
    st.subheader("📝 AI Summary")
    with st.spinner("Generating AI summary…"):
        summary = get_ai_summary(
            ticker=ticker,
            latest_close=latest_close,
            period_high=df["Close"].max(),
            period_low=df["Close"].min(),
            avg_volume=df["Volume"].mean(),
            pct_change=day_change,
            last_10_closes=df["Close"].tail(10).tolist(),
            period=period,
        )
    st.info(summary)
    st.markdown("---")

    # Candlestick
    st.subheader("Price Action")
    fig_c = go.Figure(go.Candlestick(
        x=df.index, open=df["Open"], high=df["High"],
        low=df["Low"], close=df["Close"],
    ))
    fig_c.update_layout(template="plotly_dark", height=500,
                        xaxis_rangeslider_visible=False,
                        yaxis_title=f"Price ({price_unit})")
    st.plotly_chart(fig_c, use_container_width=True)

    # Moving Averages
    st.subheader("Trend Indicators (Moving Averages)")
    fig_ma = go.Figure()
    fig_ma.add_trace(go.Scatter(x=df.index, y=df["Close"],  name="Close",    line=dict(width=2)))
    fig_ma.add_trace(go.Scatter(x=df.index, y=df["50MA"],   name="50-day MA", line=dict(dash="dash")))
    fig_ma.add_trace(go.Scatter(x=df.index, y=df["200MA"],  name="200-day MA",line=dict(dash="dot")))
    fig_ma.update_layout(template="plotly_dark", yaxis_title=f"Price ({price_unit})", hovermode="x unified")
    st.plotly_chart(fig_ma, use_container_width=True)

    # Volume
    st.subheader("Volume Analysis")
    st.plotly_chart(px.bar(df, x=df.index, y="Volume", template="plotly_dark"),
                    use_container_width=True)

    # Daily Returns
    st.subheader("Daily Returns (%)")
    fig_r = px.line(df, x=df.index, y="Daily Return", template="plotly_dark")
    fig_r.update_layout(yaxis_tickformat=".2%")
    st.plotly_chart(fig_r, use_container_width=True)


# MULTI-STOCK COMPARISON VIEW
else:
    st.markdown("<h2 style='text-align:center;'>Multi-Stock Comparison</h2>", unsafe_allow_html=True)

    # ── Metric cards ──────────────────────────────────────────────────────────
    cols = st.columns(min(len(data), 4))
    for i, (ticker, df) in enumerate(data.items()):
        close  = df["Close"].iloc[-1]
        pct    = ((close - df["Close"].iloc[-2]) / df["Close"].iloc[-2] * 100) if len(df) > 1 else 0.0
        curr   = stock_currencies.get(ticker, "INR")
        unit   = "₹" if curr in {"INR"} | CONVERTIBLE_CURRENCIES else curr
        cols[i % 4].metric(ticker, f"{unit} {close:,.2f}", f"{pct:+.2f}%")

    st.markdown("---")

    # ── Normalized performance ────────────────────────────────────────────────
    st.subheader("📈 Normalized Performance (%)")
    fig_norm = go.Figure()
    for ticker, df in data.items():
        if len(df) > 0:
            norm = ((df["Close"] / df["Close"].iloc[0]) - 1) * 100
            fig_norm.add_trace(go.Scatter(x=df.index, y=norm, name=ticker, mode="lines"))
    fig_norm.add_hline(y=0, line_dash="dot", line_color="gray", opacity=0.5)
    fig_norm.update_layout(template="plotly_dark", yaxis_title="Return (%)",
                           hovermode="x unified", height=500)
    st.plotly_chart(fig_norm, use_container_width=True)

    # ── Absolute price ────────────────────────────────────────────────────────
    st.subheader("💰 Absolute Price (₹ / Converted)")
    fig_abs = go.Figure()
    for ticker, df in data.items():
        fig_abs.add_trace(go.Scatter(x=df.index, y=df["Close"], name=ticker, mode="lines"))
    fig_abs.update_layout(template="plotly_dark", yaxis_title="Price (INR or converted)",
                          hovermode="x unified")
    st.plotly_chart(fig_abs, use_container_width=True)

    # ── Volume comparison ─────────────────────────────────────────────────────
    st.subheader("📊 Volume Comparison")
    fig_vol = go.Figure()
    for ticker, df in data.items():
        fig_vol.add_trace(go.Bar(x=df.index, y=df["Volume"], name=ticker, opacity=0.7))
    fig_vol.update_layout(template="plotly_dark", barmode="group",
                          yaxis_title="Volume", hovermode="x unified")
    st.plotly_chart(fig_vol, use_container_width=True)

    # ── Correlation heatmap ───────────────────────────────────────────────────
    if len(data) >= 2:
        st.subheader("🔗 Price Correlation Heatmap")
        closes = pd.DataFrame({t: df["Close"] for t, df in data.items()})
        corr   = closes.corr()
        fig_corr = go.Figure(go.Heatmap(
            z=corr.values,
            x=corr.columns.tolist(),
            y=corr.index.tolist(),
            colorscale="RdYlGn",
            zmin=-1, zmax=1,
            text=[[f"{v:.2f}" for v in row] for row in corr.values],
            texttemplate="%{text}",
        ))
        fig_corr.update_layout(template="plotly_dark", height=400)
        st.plotly_chart(fig_corr, use_container_width=True)

    # ── Daily returns comparison ──────────────────────────────────────────────
    st.subheader("📉 Daily Returns Comparison (%)")
    fig_ret = go.Figure()
    for ticker, df in data.items():
        ret = df["Close"].pct_change() * 100
        fig_ret.add_trace(go.Scatter(x=df.index, y=ret, name=ticker, mode="lines", opacity=0.8))
    fig_ret.update_layout(template="plotly_dark", yaxis_title="Daily Return (%)",
                          hovermode="x unified")
    st.plotly_chart(fig_ret, use_container_width=True)

    # ── Summary stats table ───────────────────────────────────────────────────
    st.subheader("📋 Summary Statistics")
    rows = []
    for ticker, df in data.items():
        curr  = stock_currencies.get(ticker, "INR")
        unit  = "₹" if curr in {"INR"} | CONVERTIBLE_CURRENCIES else curr
        pct   = ((df["Close"].iloc[-1] - df["Close"].iloc[-2]) / df["Close"].iloc[-2] * 100) if len(df) > 1 else 0.0
        total = ((df["Close"].iloc[-1] - df["Close"].iloc[0])  / df["Close"].iloc[0]  * 100)
        rows.append({
            "Ticker":        ticker,
            "Last Price":    f"{unit} {df['Close'].iloc[-1]:,.2f}",
            "Day Change":    f"{pct:+.2f}%",
            "Period Return": f"{total:+.2f}%",
            "Period High":   f"{unit} {df['Close'].max():,.2f}",
            "Period Low":    f"{unit} {df['Close'].min():,.2f}",
            "Avg Volume":    f"{df['Volume'].mean():,.0f}",
        })
    st.dataframe(pd.DataFrame(rows).set_index("Ticker"), use_container_width=True)