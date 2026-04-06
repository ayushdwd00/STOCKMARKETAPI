import streamlit as st
import yfinance as yf
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px

# ---------------- PAGE CONFIG ----------------
st.set_page_config(page_title="Premium Stock Dashboard", layout="wide")

# ---------------- CUSTOM CSS ----------------
st.markdown("""
<style>
body {
    background: linear-gradient(135deg, #0f2027, #203a43, #2c5364);
}
.main {
    background: rgba(0, 0, 0, 0.4);
    padding: 2rem;
    border-radius: 20px;
}
h1 {
    font-size: 2.8rem !important;
    font-weight: 800;
    text-align: center;
}
h2 {
    text-align: center;
}
.stMetric {
    background: rgba(255, 255, 255, 0.05);
    padding: 20px;
    border-radius: 15px;
    text-align: center;
    transition: transform 0.2s ease-in-out;
}
.stMetric:hover {
    transform: translateY(-5px);
    box-shadow: 0 10px 15px rgba(255,255,255,0.05);
}
section[data-testid="stSidebar"] {
    background-color: rgba(0,0,0,0.85);
}
</style>
""", unsafe_allow_html=True)

# ---------------- TITLE ----------------
st.markdown("<h1>Live Stock Market Dashboard</h1>", unsafe_allow_html=True)
st.markdown("<p style='text-align:center;'>Real-Time Market Intelligence</p>", unsafe_allow_html=True)

# ---------------- SIDEBAR ----------------
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
    "HDFC Bank (HDFCBANK.NS)": "HDFCBANK.NS"
}

selected_stock_names = st.sidebar.multiselect(
    "Choose Popular Stocks",
    list(popular_stocks.keys()),
    default=[list(popular_stocks.keys())[0]]
)

tickers = [popular_stocks[name] for name in selected_stock_names]

custom_tickers = st.sidebar.text_input("Or Enter Custom Tickers (comma-separated)")

if custom_tickers:
    custom_list = [t.strip().upper() for t in custom_tickers.split(',') if t.strip()]
    tickers.extend(custom_list)

# Remove duplicates while preserving order
tickers = list(dict.fromkeys(tickers))

if not tickers:
    st.warning("Please select at least one stock.")
    st.stop()

period = st.sidebar.selectbox(
    "Select Time Period",
    ["1mo", "3mo", "6mo", "1y", "2y", "5y"]
)

# ---------------- DATA FETCH ----------------
@st.cache_data(ttl=300)
def get_usd_inr(period_str):
    fx = yf.Ticker("INR=X")
    return fx.history(period=period_str)['Close']

@st.cache_data(ttl=300)
def load_data(ticker_list, time_period):
    data_dict = {}
    fx_close = None
    
    for symbol in ticker_list:
        stock = yf.Ticker(symbol)
        df = stock.history(period=time_period)
        if df.empty:
            continue
            
        try:
            currency = stock.info.get('currency', 'USD')
        except Exception:
            currency = 'USD'
            
        if currency != 'INR' and currency == 'USD':
            if fx_close is None:
                fx_close = get_usd_inr(time_period)
                
            fx_reindexed = fx_close.reindex(df.index, method='ffill').bfill()
            
            for col in ['Open', 'High', 'Low', 'Close']:
                df[col] = df[col] * fx_reindexed
                
        data_dict[symbol] = df
    return data_dict

data = load_data(tickers, period)

# ---------------- DASHBOARD ----------------
if not data:
    st.error("Invalid ticker symbol(s) or no data found.")
elif len(data) == 1:
    ticker = list(data.keys())[0]
    df = data[ticker]

    df["50MA"] = df["Close"].rolling(50).mean()
    df["200MA"] = df["Close"].rolling(200).mean()
    df["Daily Return"] = df["Close"].pct_change()

    st.markdown(f"<h2>{ticker} Market Overview</h2>", unsafe_allow_html=True)

    # ---- Metrics ----
    col1, col2, col3 = st.columns(3)

    col1.metric("Current Price", f"₹ {df['Close'].iloc[-1]:.2f}")
    col2.metric("Highest Price", f"₹ {df['Close'].max():.2f}")
    col3.metric("Lowest Price", f"₹ {df['Close'].min():.2f}")

    st.markdown("---")

    # ---- Candlestick Chart ----
    st.subheader("Price Action")

    fig_candle = go.Figure(data=[go.Candlestick(
        x=df.index,
        open=df['Open'],
        high=df['High'],
        low=df['Low'],
        close=df['Close']
    )])

    fig_candle.update_layout(template="plotly_dark", height=600)
    st.plotly_chart(fig_candle, use_container_width=True)

    # ---- Moving Averages ----
    st.subheader("Trend Indicators")

    fig_ma = go.Figure()
    fig_ma.add_trace(go.Scatter(x=df.index, y=df["Close"], name="Close"))
    fig_ma.add_trace(go.Scatter(x=df.index, y=df["50MA"], name="50 MA"))
    fig_ma.add_trace(go.Scatter(x=df.index, y=df["200MA"], name="200 MA"))

    fig_ma.update_layout(template="plotly_dark")
    st.plotly_chart(fig_ma, use_container_width=True)

    # ---- Volume ----
    st.subheader("Volume Analysis")

    fig_vol = px.bar(df, x=df.index, y="Volume", template="plotly_dark")
    st.plotly_chart(fig_vol, use_container_width=True)

    # ---- Daily Returns ----
    st.subheader("Daily Returns")

    fig_ret = px.line(df, x=df.index, y="Daily Return", template="plotly_dark")
    st.plotly_chart(fig_ret, use_container_width=True)

else:
    st.markdown("<h2>Multi-Stock Comparison</h2>", unsafe_allow_html=True)
    
    # ---- Metrics Grid ----
    cols = st.columns(min(len(data), 4))
    for i, (ticker, df) in enumerate(data.items()):
        col_idx = i % 4
        close_price = df['Close'].iloc[-1]
        pct_change = 0
        if len(df) > 1:
            pct_change = (close_price - df['Close'].iloc[-2]) / df['Close'].iloc[-2] * 100
        cols[col_idx].metric(ticker, f"₹ {close_price:.2f}", f"{pct_change:.2f}%")
        
    st.markdown("---")
    
    # ---- Normalized Comparison Chart ----
    st.subheader("Normalized Performance (%)")
    fig_comp = go.Figure()
    
    for ticker, df in data.items():
        if len(df) > 0:
            first_close = df['Close'].iloc[0]
            normalized = ((df['Close'] / first_close) - 1) * 100
            fig_comp.add_trace(go.Scatter(x=df.index, y=normalized, name=ticker, mode='lines'))
            
    fig_comp.update_layout(
        template="plotly_dark",
        yaxis_title="Percentage Change (%)",
        hovermode="x unified",
        height=600
    )
    st.plotly_chart(fig_comp, use_container_width=True)
    
    # ---- Absolute Price Comparison Chart ----
    st.subheader("Absolute Price Comparison (₹)")
    fig_abs = go.Figure()
    for ticker, df in data.items():
        fig_abs.add_trace(go.Scatter(x=df.index, y=df['Close'], name=ticker, mode='lines'))
    fig_abs.update_layout(template="plotly_dark", yaxis_title="Price (INR)", hovermode="x unified")
    st.plotly_chart(fig_abs, use_container_width=True)