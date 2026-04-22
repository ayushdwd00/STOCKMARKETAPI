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

# Page configuration
st.set_page_config(page_title="Stock Dashboard", layout="wide")

# GROQ client setup
api_key = os.getenv("GROQ_API_KEY") or st.secrets.get("GROQ_API_KEY")
if not api_key:
    st.error("⚠️ GROQ_API_KEY not found.")
    st.stop()

client = Groq(api_key=api_key)

# Functions
def get_ticker_from_llm(company_name: str) -> str:
    prompt = f"""Return ONLY stock ticker symbol.

Company: {company_name}
Ticker:"""
    try:
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=20,
        )
        return response.choices[0].message.content.strip().upper()
    except:
        return None


def get_ai_summary(ticker, latest_close, high, low, avg_vol, pct, last10, period):
    prompt = f"""Summarize stock briefly:

Ticker: {ticker}
Price: {latest_close}
High: {high}
Low: {low}
Volume: {avg_vol}
Change: {pct}%
"""
    try:
        res = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=120,
        )
        return res.choices[0].message.content
    except:
        return "Summary unavailable"


# Title
st.markdown("<h1 style='text-align:center;'>Live Stock Market Dashboard</h1>", unsafe_allow_html=True)

# Refresh button
if st.button("🔄 Refresh Data"):
    st.cache_data.clear()
    st.rerun()

# Sidebar
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
}

selection = st.sidebar.selectbox(
    "Search or Select Stock",
    ["Select or type below"] + list(popular_stocks.keys())
)

manual_input = st.sidebar.text_input(
    "",
    placeholder="Type company or ticker"
)

if "selected_ticker" not in st.session_state:
    st.session_state.selected_ticker = None

if manual_input:
    user = manual_input.strip().upper()
    if "." in user or len(user) <= 5:
        st.session_state.selected_ticker = user
    else:
        ticker = get_ticker_from_llm(user)
        if ticker:
            st.session_state.selected_ticker = ticker
            st.sidebar.success(f"Found: {ticker}")

elif selection != "Select or type below":
    st.session_state.selected_ticker = popular_stocks[selection]

ticker = st.session_state.selected_ticker

if not ticker:
    st.warning("Please select or enter a stock.")
    st.stop()

tickers = [ticker]

# Time frame
period = st.sidebar.selectbox(
    "Time Frame",
    ["1mo", "3mo", "6mo", "1y", "2y", "5y"]
)

# Comparison
compare_input = st.sidebar.text_input("Add Comparison (optional)")

if compare_input:
    user = compare_input.strip().upper()
    if "." in user or len(user) <= 5:
        tickers.append(user)
    else:
        comp = get_ticker_from_llm(user)
        if comp:
            tickers.append(comp)
            st.sidebar.success(f"Added: {comp}")

# Data loading
@st.cache_data(ttl=300)
def load_data(tickers, period):
    data = {}
    for t in tickers:
        df = yf.Ticker(t).history(period=period)
        if not df.empty:
            data[t] = df
    return data

data = load_data(tuple(tickers), period)

if not data:
    st.error("No data found")
    st.stop()

# Dashboard
if len(data) == 1:
    t = list(data.keys())[0]
    df = data[t]

    st.subheader(f"{t} Overview")

    col1, col2, col3 = st.columns(3)
    col1.metric("Price", f"{df['Close'].iloc[-1]:.2f}")
    col2.metric("High", f"{df['Close'].max():.2f}")
    col3.metric("Low", f"{df['Close'].min():.2f}")

    st.subheader("AI Summary")

    summary = get_ai_summary(
        t,
        df["Close"].iloc[-1],
        df["Close"].max(),
        df["Close"].min(),
        df["Volume"].mean(),
        0,
        [],
        period,
    )

    st.info(summary)

    fig = go.Figure(data=[go.Candlestick(
        x=df.index,
        open=df["Open"],
        high=df["High"],
        low=df["Low"],
        close=df["Close"],
    )])

    fig.update_layout(template="plotly_dark")
    st.plotly_chart(fig, use_container_width=True)

else:
    st.subheader("Comparison")

    fig = go.Figure()

    for t, df in data.items():
        norm = df["Close"] / df["Close"].iloc[0]
        fig.add_trace(go.Scatter(x=df.index, y=norm, name=t))

    fig.update_layout(template="plotly_dark")
    st.plotly_chart(fig, use_container_width=True)