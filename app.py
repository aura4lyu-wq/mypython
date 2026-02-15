import streamlit as st
import pandas as pd
import yfinance as yf
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
import squarify
import numpy as np
import os
import glob

# フォント設定（環境に応じたフォールバック）
font_candidates = [
    'C:/Windows/Fonts/meiryo.ttc',
    '/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc',
    '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc',
    '/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc',
]
for fp in font_candidates:
    if os.path.exists(fp):
        font_prop = fm.FontProperties(fname=fp)
        plt.rcParams['font.family'] = font_prop.get_name()
        break

st.set_page_config(page_title="ポートフォリオ可視化", layout="wide")

# --- グローバルCSS: 1920x1080向けコンパクト化 ---
st.markdown("""
<style>
    /* メインコンテンツの上部余白を縮小 */
    .block-container { padding-top: 1rem; padding-bottom: 0rem; }
    /* 見出しの余白を縮小 */
    h1, h2, h3 { margin-top: 0.2rem; margin-bottom: 0.2rem; }
    /* st.metric の余白を縮小 */
    [data-testid="stMetric"] { padding: 0.3rem 0; }
    [data-testid="stMetricLabel"] { font-size: 0.8rem; }
    [data-testid="stMetricValue"] { font-size: 1.3rem; }
    /* ボタンを小さく */
    .stButton > button { padding: 0.15rem 0.6rem; font-size: 0.8rem; }
    /* dataframeの行高さを縮小 */
    .stDataFrame { font-size: 0.85rem; }
    /* expanderの余白縮小 */
    .streamlit-expanderHeader { padding: 0.3rem 0; }
    /* container内の余白を縮小 */
    [data-testid="stVerticalBlock"] > div { gap: 0.3rem; }
    /* number_inputを小さく */
    .stNumberInput label { font-size: 0.85rem; margin-bottom: 0; }
    .stNumberInput input { padding: 0.25rem 0.5rem; }
    /* 区切り線の余白 */
    hr { margin: 0.3rem 0; }
</style>
""", unsafe_allow_html=True)

st.markdown("#### SBI証券 ポートフォリオ")

cash_col1, cash_col2 = st.columns(2)
with cash_col1:
    cash_usd = st.number_input("現金 (USD)", min_value=0.0, value=0.0, step=100.0)
with cash_col2:
    cash_jpy = st.number_input("現金 (日本円)", min_value=0.0, value=0.0, step=100.0)


# --- キャッシュ付きデータ取得関数 ---

@st.cache_data(ttl=300)
def fetch_stock_info(ticker):
    """銘柄情報を取得（5分キャッシュ）"""
    try:
        data = yf.Ticker(ticker).info
        return {
            "price": data.get("regularMarketPrice"),
            "prev_close": data.get("previousClose"),
            "sector": data.get("sector", "その他"),
            "per": data.get("trailingPE"),
            "forward_per": data.get("forwardPE"),
            "pbr": data.get("priceToBook"),
            "dividend_yield": data.get("dividendYield"),
            "eps": data.get("trailingEps"),
            "market_cap": data.get("marketCap"),
        }
    except Exception:
        return {
            "price": None, "prev_close": None, "sector": "その他",
            "per": None, "forward_per": None, "pbr": None,
            "dividend_yield": None, "eps": None, "market_cap": None,
        }


@st.cache_data(ttl=300)
def fetch_history(ticker, period="6mo"):
    """過去の価格推移を取得（5分キャッシュ）"""
    try:
        hist = yf.Ticker(ticker).history(period=period)
        return hist["Close"]
    except Exception:
        return pd.Series(dtype=float)


@st.cache_data(ttl=300)
def fetch_fx_rate():
    try:
        return yf.Ticker("JPY=X").info["regularMarketPrice"]
    except Exception:
        return 150.0


# --- ファイル読み込み ---

with st.expander("CSVファイルをアップロード／自動選択"):
    uploaded_file = st.file_uploader("SBI証券の約定履歴CSVをアップロード", type="csv")

    if uploaded_file is None:
        csv_files = glob.glob("*.csv")
        if csv_files:
            latest_file = max(csv_files, key=os.path.getmtime)
            uploaded_file = open(latest_file, "rb")
            st.success(f"自動読み込み: {latest_file}")
        else:
            st.warning("CSVファイルをアップロードしてください（またはCSVファイルをこのフォルダに置いてください）")

if uploaded_file:
    df = pd.read_csv(uploaded_file, encoding="shift_jis", skiprows=6)
    df.columns = df.columns.str.strip().str.replace('"', '')
    df = df[["銘柄コード", "取引", "約定数量", "約定単価"]].copy()
    df["約定単価"] = df["約定単価"].str.replace("USD", "").str.replace(",", "").astype(float)
    df["約定数量"] = df["約定数量"].astype(int)
    df["signed_qty"] = df.apply(lambda row: row["約定数量"] if row["取引"] == "現買" else -row["約定数量"], axis=1)
    df["金額"] = df["約定単価"] * df["signed_qty"]

    position = df.groupby("銘柄コード").agg({"signed_qty": "sum", "金額": "sum"})
    position = position[position["signed_qty"] > 0]
    position["平均取得単価"] = position["金額"] / position["signed_qty"]

    tickers = position.index.tolist()
    current_prices = {}
    previous_closes = {}
    sectors = {}
    pers = {}
    forward_pers = {}
    pbrs = {}
    dividend_yields = {}
    epss = {}
    market_caps = {}
    for ticker in tickers:
        info = fetch_stock_info(ticker)
        current_prices[ticker] = info["price"]
        previous_closes[ticker] = info["prev_close"]
        sectors[ticker] = info["sector"]
        pers[ticker] = info["per"]
        forward_pers[ticker] = info["forward_per"]
        pbrs[ticker] = info["pbr"]
        dividend_yields[ticker] = info["dividend_yield"]
        epss[ticker] = info["eps"]
        market_caps[ticker] = info["market_cap"]

    position["現在株価"] = position.index.map(current_prices)
    position["前日終値"] = position.index.map(previous_closes)
    position["セクター"] = position.index.map(sectors)
    position["PER"] = position.index.map(pers)
    position["予想PER"] = position.index.map(forward_pers)
    position["PBR"] = position.index.map(pbrs)
    position["配当利回り"] = position.index.map(dividend_yields)
    position["EPS"] = position.index.map(epss)
    position["時価総額"] = position.index.map(market_caps)
    position.dropna(subset=["現在株価", "前日終値"], inplace=True)
    position["評価額"] = position["signed_qty"] * position["現在株価"]
    position["騰落率"] = (position["現在株価"] - position["前日終値"]) / position["前日終値"]

    # --- リスク指標の計算 ---
    spy_hist = fetch_history("SPY", "6mo")
    spy_returns = spy_hist.pct_change().dropna()

    stock_volatilities = {}
    stock_betas = {}
    portfolio_weights = {}
    stock_returns_dict = {}

    total_eval = position["評価額"].sum()

    for ticker in position.index:
        hist = fetch_history(ticker, "6mo")
        if len(hist) > 20:
            returns = hist.pct_change().dropna()
            stock_returns_dict[ticker] = returns
            # 年率換算ボラティリティ
            stock_volatilities[ticker] = returns.std() * np.sqrt(252)
            # β値（SPYとの共分散 / SPYの分散）
            aligned = pd.concat([returns, spy_returns], axis=1, join="inner")
            aligned.columns = ["stock", "spy"]
            if len(aligned) > 20:
                cov = aligned.cov()
                stock_betas[ticker] = cov.loc["stock", "spy"] / cov.loc["spy", "spy"]
            else:
                stock_betas[ticker] = None
        else:
            stock_volatilities[ticker] = None
            stock_betas[ticker] = None

        portfolio_weights[ticker] = position.at[ticker, "評価額"] / total_eval if total_eval > 0 else 0

    position["ボラティリティ"] = position.index.map(stock_volatilities)
    position["β値"] = position.index.map(stock_betas)

    # ポートフォリオ全体のリスク指標
    if stock_returns_dict:
        returns_df = pd.DataFrame(stock_returns_dict).fillna(0)
        weights = pd.Series({t: portfolio_weights[t] for t in returns_df.columns})
        weights = weights / weights.sum()
        portfolio_daily_returns = returns_df.mul(weights).sum(axis=1)

        portfolio_annual_return = portfolio_daily_returns.mean() * 252
        portfolio_annual_vol = portfolio_daily_returns.std() * np.sqrt(252)
        risk_free_rate = 0.045
        sharpe_ratio = (portfolio_annual_return - risk_free_rate) / portfolio_annual_vol if portfolio_annual_vol > 0 else 0

        # ポートフォリオβ値（加重平均）
        portfolio_beta = sum(
            portfolio_weights[t] * (stock_betas.get(t) or 0)
            for t in position.index
        )

        # 最大ドローダウン
        cumulative = (1 + portfolio_daily_returns).cumprod()
        peak = cumulative.cummax()
        drawdown = (cumulative - peak) / peak
        max_drawdown = drawdown.min()
    else:
        portfolio_annual_return = 0
        portfolio_annual_vol = 0
        sharpe_ratio = 0
        portfolio_beta = 0
        max_drawdown = 0

    # --- セッション状態 ---
    if "hidden_tickers" not in st.session_state:
        st.session_state.hidden_tickers = set()
    if "show_yen" not in st.session_state:
        st.session_state.show_yen = False

    sorted_position = position.sort_values("評価額", ascending=False).copy()
    fx_rate = fetch_fx_rate()

    # =============================
    # リスク指標サマリー
    # =============================
    st.markdown("##### リスク指標")
    risk_col1, risk_col2, risk_col3, risk_col4 = st.columns(4)
    with risk_col1:
        st.metric("ポートフォリオβ値", f"{portfolio_beta:.2f}",
                  help="1.0 = 市場(S&P500)と同じリスク。1超は市場より高リスク")
    with risk_col2:
        st.metric("年率ボラティリティ", f"{portfolio_annual_vol * 100:.1f}%",
                  help="ポートフォリオ全体の年率換算変動率（過去6ヶ月）")
    with risk_col3:
        st.metric("シャープレシオ", f"{sharpe_ratio:.2f}",
                  help="リスク調整後リターン。高いほどリスクに対するリターンが効率的")
    with risk_col4:
        st.metric("最大ドローダウン", f"{max_drawdown * 100:.1f}%",
                  help="過去6ヶ月間の最大下落幅")

    st.markdown("---")

    # =============================
    # メイン3カラム
    # =============================
    col1, col2, col3 = st.columns([1, 1, 1])

    with col1:
        with st.container(border=True):
            hdr_col, btn_col = st.columns([3, 1])
            hdr_col.markdown("##### 保有銘柄詳細")
            if btn_col.button("USD⇔JPY", use_container_width=True):
                st.session_state.show_yen = not st.session_state.show_yen

            total_value = sorted_position[~sorted_position.index.isin(st.session_state.hidden_tickers)]["評価額"].sum()
            total_value = total_value + cash_usd + (cash_jpy / fx_rate)

            if st.session_state.show_yen:
                fmt_total = f"{total_value * fx_rate:,.0f}"
                currency_label = "円"
            else:
                fmt_total = f"{total_value:,.2f}"
                currency_label = "USD"

            st.markdown(f"""
            <div style='text-align: center; margin: 0.2rem 0;'>
                <span style='font-size:11px; color:#888;'>総評価額</span><br>
                <span style='font-size:22px; font-weight: bold;'>
                    {fmt_total} <span style='font-size:11px;'>{currency_label}</span>
                </span>
            </div>
            """, unsafe_allow_html=True)

            for ticker in sorted_position.index:
                is_hidden = ticker in st.session_state.hidden_tickers
                eval_value = sorted_position.at[ticker, "評価額"] if not is_hidden else 0

                if st.session_state.show_yen:
                    eval_display = f"{eval_value * fx_rate:,.0f}"
                else:
                    eval_display = f"{eval_value:,.2f}"

                percent = (eval_value / total_value * 100) if total_value > 0 else 0

                cols = st.columns([2, 3, 1])
                cols[0].markdown(f"<span style='font-size:13px; font-weight:600;'>{ticker}</span>", unsafe_allow_html=True)
                cols[1].markdown(
                    f"<span style='font-size:12px;'>{eval_display} ({percent:.1f}%)</span>",
                    unsafe_allow_html=True
                )
                label = "非表示" if not is_hidden else "再表示"
                if cols[2].button(label, key=f"toggle_{ticker}", use_container_width=True):
                    if is_hidden:
                        st.session_state.hidden_tickers.remove(ticker)
                    else:
                        st.session_state.hidden_tickers.add(ticker)
                    st.rerun()
            if st.button("すべて再表示"):
                st.session_state.hidden_tickers.clear()
                st.rerun()

    # --- 色分け関数 ---

    def classify_color(rate):
        if rate >= 0.02:
            return "#66ff66"
        elif rate >= 0.01:
            return "#339933"
        elif rate > 0.0:
            return "#003300"
        elif rate == 0.0:
            return "#e0e0e0"
        elif rate > -0.01:
            return "#6e4e4e"
        elif rate > -0.02:
            return "#cc3333"
        else:
            return "#ff6666"

    display_position = sorted_position[~sorted_position.index.isin(st.session_state.hidden_tickers)].copy()

    with col2:
        with st.container(border=True):
            st.markdown("##### 銘柄別保有比率")
            pie_data = display_position["評価額"].copy()
            pie_data.loc["現金"] = cash_usd + (cash_jpy / fx_rate)
            labels = pie_data.index.tolist()
            sizes = pie_data.tolist()

            base_colors = plt.cm.tab20.colors
            color_map = ["#555555" if label == "現金" else base_colors[i % len(base_colors)] for i, label in enumerate(labels)]

            fig1, ax1 = plt.subplots(figsize=(4, 2.8), facecolor='#0E1117')
            ax1.pie(
                sizes, labels=labels, colors=color_map,
                startangle=90, autopct='%1.1f%%', counterclock=False,
                textprops={"fontsize": 7, "color": "white"},
                wedgeprops={'linewidth': 0.5, 'edgecolor': "white"}
            )
            ax1.axis('equal')
            st.pyplot(fig1, use_container_width=True)

        with st.container(border=True):
            st.markdown("##### セクター別保有比率")
            sector_group = display_position.groupby("セクター")["評価額"].sum().sort_values(ascending=False)
            sector_group.loc["現金"] = cash_usd + (cash_jpy / fx_rate)
            sector_labels = []
            for sector in sector_group.index:
                tickers_in_sector = display_position[display_position["セクター"] == sector].index.tolist()
                label = f"{sector}\n({', '.join(tickers_in_sector)})"
                sector_labels.append(label)
            fig3, ax3 = plt.subplots(figsize=(4, 2.8), facecolor='#0E1117')
            ax3.pie(
                sector_group, labels=sector_labels,
                startangle=90, autopct='%1.1f%%', counterclock=False,
                textprops={"fontsize": 5, "color": "white"},
                wedgeprops={'linewidth': 0.5, 'edgecolor': "white"}
            )
            ax3.axis('equal')
            st.pyplot(fig3, use_container_width=True)

    with col3:
        with st.container(border=True):
            st.markdown("##### 前日比騰落率ヒートマップ")
            if len(display_position) > 0:
                values = display_position.copy()
                sizes_tm = values["評価額"]
                colors_tm = [classify_color(v) for v in values["騰落率"]]
                min_font, max_font = 5, 14
                min_size, max_size = min(sizes_tm), max(sizes_tm)
                font_sizes = [
                    int(min_font + (s - min_size) / (max_size - min_size) * (max_font - min_font))
                    if max_size > min_size else min_font for s in sizes_tm
                ]

                fig2, ax2 = plt.subplots(figsize=(5, 5.5), facecolor='#0E1117')
                normed_sizes = squarify.normalize_sizes(sizes_tm, 600, 400)
                rects = squarify.squarify(normed_sizes, 0, 0, 600, 400)

                for rect, color, lbl, rate, fs in zip(rects, colors_tm, values.index.tolist(), values["騰落率"], font_sizes):
                    x, y, dx, dy = rect['x'], rect['y'], rect['dx'], rect['dy']
                    ax2.add_patch(plt.Rectangle((x, y), dx, dy, facecolor=color, edgecolor="black", linewidth=1))
                    text = f"{lbl}\n{rate * 100:.2f}%"
                    if fs < 5 or dx < 15 or dy < 15:
                        continue
                    ax2.text(x + dx / 2, y + dy / 2, text, color='white', ha='center', va='center', fontsize=fs)

                ax2.set_xlim(0, 600)
                ax2.set_ylim(0, 400)
                ax2.invert_yaxis()
                ax2.axis('off')
                st.pyplot(fig2, use_container_width=True)

    # =============================
    # 銘柄別リスク・バリュエーション指標
    # =============================
    st.markdown("##### 銘柄別リスク・バリュエーション指標")
    metrics_df = position[["セクター", "評価額"]].copy()
    metrics_df["構成比"] = metrics_df["評価額"] / metrics_df["評価額"].sum()
    metrics_df["β値"] = position["β値"]
    metrics_df["年率Vol"] = position["ボラティリティ"]
    metrics_df["PER"] = position["PER"]
    metrics_df["予想PER"] = position["予想PER"]
    metrics_df["PBR"] = position["PBR"]
    metrics_df["配当利回り"] = position["配当利回り"]
    metrics_df["EPS"] = position["EPS"]
    metrics_df["時価総額(B$)"] = position["時価総額"].apply(
        lambda v: v / 1e9 if pd.notna(v) else None
    )

    fmt_or_na = lambda fmt: (lambda v: fmt.format(v) if pd.notna(v) else "N/A")

    st.dataframe(
        metrics_df.style.format({
            "評価額": "${:,.2f}",
            "構成比": "{:.1%}",
            "β値": fmt_or_na("{:.2f}"),
            "年率Vol": fmt_or_na("{:.1%}"),
            "PER": fmt_or_na("{:.1f}"),
            "予想PER": fmt_or_na("{:.1f}"),
            "PBR": fmt_or_na("{:.1f}"),
            "配当利回り": fmt_or_na("{:.2%}"),
            "EPS": fmt_or_na("${:.2f}"),
            "時価総額(B$)": fmt_or_na("{:.1f}"),
        }),
        use_container_width=True,
    )

    # =============================
    # 詳細データフレーム
    # =============================
    with st.expander("詳細データフレーム"):
        detail_cols = ["signed_qty", "金額", "平均取得単価", "現在株価", "前日終値", "評価額", "騰落率", "セクター"]
        st.dataframe(
            position[detail_cols].style.format({
                "signed_qty": "{:,.0f}",
                "金額": "${:,.2f}",
                "平均取得単価": "${:,.2f}",
                "現在株価": "${:,.2f}",
                "前日終値": "${:,.2f}",
                "評価額": "${:,.2f}",
                "騰落率": "{:.2%}",
            })
        )

else:
    st.info("CSVファイルをアップロードしてください。")
