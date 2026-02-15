import streamlit as st
import pandas as pd
import yfinance as yf
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
import squarify
import matplotlib.colors as mcolors
import numpy as np
import os
import glob

# フォント設定
font_path = 'C:/Windows/Fonts/meiryo.ttc'
font_prop = fm.FontProperties(fname=font_path)
plt.rcParams['font.family'] = font_prop.get_name()

st.set_page_config(page_title="ポートフォリオ可視化", layout="wide")
st.title("SBI証券 ポートフォリオ")

cash_usd = st.number_input("現金保有額 (USD)", min_value=0.0, value=0.0, step=100.0)
cash_jpy = st.number_input("現金保有額 (日本円)", min_value=0.0, value=0.0, step=100.0)

# ファイルアップロード（手動 or 自動で最新ファイルを選択）
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
    for ticker in tickers:
        try:
            data = yf.Ticker(ticker).info
            current_prices[ticker] = data.get("regularMarketPrice")
            previous_closes[ticker] = data.get("previousClose")
            sectors[ticker] = data.get("sector", "その他")
        except:
            current_prices[ticker] = None
            previous_closes[ticker] = None
            sectors[ticker] = "その他"

    position["現在株価"] = position.index.map(current_prices)
    position["前日終値"] = position.index.map(previous_closes)
    position["セクター"] = position.index.map(sectors)
    position.dropna(subset=["現在株価", "前日終値"], inplace=True)
    position["評価額"] = position["signed_qty"] * position["現在株価"]
    position["含み損益"] = position["評価額"] - (position["signed_qty"] * position["平均取得単価"])
    position["騰落率"] = (position["現在株価"] - position["前日終値"]) / position["前日終値"]

    if "hidden_tickers" not in st.session_state:
        st.session_state.hidden_tickers = set()
    if "show_yen" not in st.session_state:
        st.session_state.show_yen = False

    sorted_position = position.sort_values("評価額", ascending=False).copy()
    col1, col2, col3 = st.columns([1, 1, 1])

    with col1:
        with st.container(border=True):
            st.markdown("### 保有銘柄詳細")

            if st.button("USD⇔JPY"):
                st.session_state.show_yen = not st.session_state.show_yen

            try:
                fx_rate = yf.Ticker("JPY=X").info["regularMarketPrice"]
            except:
                fx_rate = 150.0
                
            total_value = sorted_position[~sorted_position.index.isin(st.session_state.hidden_tickers)]["評価額"].sum()
            total_value = total_value + cash_usd + (cash_jpy / fx_rate)
            
            if st.session_state.show_yen:
                total_value = total_value * fx_rate
                st.markdown(f"""
                <div style='text-align: center;'>
                    <p style='font-size:13px; margin: 0;'>株式総評価額</p>
                    <p style='font-size:32px; font-weight: bold; margin: 0;'>
                        {total_value:,.0f} <span style='font-size:14px;'>円</span>
                    </p>
                </div><br>
                """, unsafe_allow_html=True)
            else:
                total_value = total_value
                st.markdown(f"""
                <div style='text-align: center;'>
                    <p style='font-size:13px; margin: 0;'>株式総評価額</p>
                    <p style='font-size:32px; font-weight: bold; margin: 0;'>
                        {total_value:,.2f} <span style='font-size:14px;'>USD</span>
                    </p>
                </div><br>
                """, unsafe_allow_html=True)
                

            for ticker in sorted_position.index:
                is_hidden = ticker in st.session_state.hidden_tickers
                eval_value = sorted_position.at[ticker, "評価額"] if not is_hidden else 0
                if st.session_state.show_yen:
                    eval_display = f"{eval_value * fx_rate:,.0f}"
                else:
                    eval_display = f"{eval_value:,.2f}"
                percent = (eval_value / total_value * 100) if total_value > 0 else 0
                cols = st.columns([2, 3, 2])
                cols[0].markdown(f"<span style='font-size:18px;'>{ticker}</span>", unsafe_allow_html=True)
                cols[1].markdown(f"<span style='font-size:16px;'>{eval_display} ({percent:.1f}%)</span>", unsafe_allow_html=True)
                button_style = "background-color: #28a745; color: white;" if not is_hidden else "background-color: #dc3545; color: white;"
                label = "非表示" if not is_hidden else "再表示"
                if cols[2].button(label, key=f"toggle_{ticker}"):
                    if is_hidden:
                        st.session_state.hidden_tickers.remove(ticker)
                    else:
                        st.session_state.hidden_tickers.add(ticker)
                    st.rerun()
            if st.button("すべて再表示"):
                st.session_state.hidden_tickers.clear()
                st.rerun()

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
            st.markdown("### 銘柄別保有比率")
            pie_data = display_position["評価額"].copy()
            pie_data.loc["現金"] = cash_usd + (cash_jpy / fx_rate)  # 現金を追加
            labels = pie_data.index.tolist()
            sizes = pie_data.tolist()

            # 色設定：現金は灰色、それ以外は自動色
            base_colors = plt.cm.tab20.colors
            color_map = ["#0E1117" if label == "現金" else base_colors[i % len(base_colors)] for i, label in enumerate(labels)]

            fig1, ax1 = plt.subplots(figsize=(3,3), facecolor='#0E1117')
            ax1.pie(
                sizes,
                labels=labels,
                colors=color_map,
                startangle=90,
                autopct='%1.1f%%',
                counterclock=False,
                textprops={"fontsize": 8, "color": "white"},
                wedgeprops={'linewidth': 0.5, 'edgecolor':"white"}
            )
            ax1.axis('equal')
            st.pyplot(fig1, use_container_width=False)

        with st.container(border=True):
            st.markdown("### セクター別保有比率")
            sector_group = display_position.groupby("セクター")["評価額"].sum().sort_values(ascending=False)
            sector_group.loc["現金"] = cash_usd + (cash_jpy / fx_rate)  # 現金を追加
            sector_labels = []
            for sector in sector_group.index:
                tickers_in_sector = display_position[display_position["セクター"] == sector].index.tolist()
                label = f"{sector}\n({', '.join(tickers_in_sector)})"
                sector_labels.append(label)
            fig3, ax3 = plt.subplots(figsize=(3,3), facecolor='#0E1117')
            ax3.pie(
                sector_group,
                labels=sector_labels,
                startangle=90,
                autopct='%1.1f%%',
                counterclock=False,
                textprops={"fontsize": 6, "color": "white"},
                wedgeprops={'linewidth': 0.5, 'edgecolor':"white"}
            )
            ax3.axis('equal')
            st.pyplot(fig3,use_container_width=False)

    with col3:
        with st.container(border=True):
            st.markdown("### 前日比騰落率ヒートマップ")
            values = display_position.copy()
            sizes = values["評価額"]
            colors = [classify_color(v) for v in values["騰落率"]]
            min_font, max_font = 6, 20
            min_size, max_size = min(sizes), max(sizes)
            font_sizes = [int(min_font + (s - min_size) / (max_size - min_size) * (max_font - min_font)) if max_size > min_size else min_font for s in sizes]

            fig2, ax2 = plt.subplots(figsize=(3,3), facecolor='#0E1117')
            normed_sizes = squarify.normalize_sizes(sizes, 600, 400)
            rects = squarify.squarify(normed_sizes, 0, 0, 600, 400)

            for rect, color, label, rate, font_size in zip(rects, colors, values.index.tolist(), values["騰落率"], font_sizes):
                x, y, dx, dy = rect['x'], rect['y'], rect['dx'], rect['dy']
                ax2.add_patch(plt.Rectangle((x, y), dx, dy, facecolor=color, edgecolor="black", linewidth=1))
                text = f"{label}\n{rate*100:.2f}%"
                if font_size < 6 or dx < 20 or dy < 20:
                    continue
                ax2.text(x + dx / 2, y + dy / 2, text, color='white', ha='center', va='center', fontsize=font_size)

            ax2.set_xlim(0, 600)
            ax2.set_ylim(0, 400)
            ax2.invert_yaxis()
            ax2.axis('off')
            st.pyplot(fig2, use_container_width=False)

    with st.expander("📋 詳細データフレーム"):
        st.dataframe(
            position.style.format({
                "signed_qty": "{:,.0f}",
                "金額": "${:,.2f}",
                "平均取得単価": "${:,.2f}",
                "現在株価": "${:,.2f}",
                "前日終値": "${:,.2f}",
                "評価額": "${:,.2f}",
                "含み損益": "${:,.2f}",
                "騰落率": "{:.2%}"
            })
        )

else:
    st.info("CSVファイルをアップロードしてください。")
