import streamlit as st
import pandas as pd
import json
import os
import glob
from datetime import datetime, timedelta

# ============================================================
# 米国株 企業分析ツール（STAGE 1〜5）
# ============================================================

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "analyses")
os.makedirs(DATA_DIR, exist_ok=True)

st.set_page_config(page_title="米国株 企業分析ツール", layout="wide")

st.markdown("""
<style>
    .block-container { padding-top: 1rem; }
    .stRadio > div { flex-direction: row; gap: 1rem; }
    div[data-testid="stExpander"] details summary p { font-size: 1.1rem; font-weight: 600; }
</style>
""", unsafe_allow_html=True)


# ============================================================
# ユーティリティ関数
# ============================================================

def get_analysis_filepath(ticker: str, date_str: str) -> str:
    safe_name = f"{ticker}_{date_str}.json"
    return os.path.join(DATA_DIR, safe_name)


def save_analysis(data: dict):
    ticker = data.get("ticker", "UNKNOWN")
    date_str = data.get("date", datetime.now().strftime("%Y-%m-%d"))
    filepath = get_analysis_filepath(ticker, date_str)
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return filepath


def load_analysis(filepath: str) -> dict:
    with open(filepath, "r", encoding="utf-8") as f:
        return json.load(f)


def list_analyses() -> list:
    files = sorted(glob.glob(os.path.join(DATA_DIR, "*.json")), reverse=True)
    results = []
    for fp in files:
        try:
            data = load_analysis(fp)
            results.append({
                "filepath": fp,
                "ticker": data.get("ticker", "?"),
                "company": data.get("company_name", "?"),
                "date": data.get("date", "?"),
                "current_stage": data.get("current_stage", 1),
                "stage1_result": data.get("stage1", {}).get("result", "未完了"),
                "stage2_score": data.get("stage2", {}).get("total_score", "-"),
                "stage5_decision": data.get("stage5", {}).get("decision", "未完了"),
            })
        except Exception:
            continue
    return results


def init_analysis_data() -> dict:
    return {
        "company_name": "",
        "ticker": "",
        "date": datetime.now().strftime("%Y-%m-%d"),
        "current_stage": 1,
        "stage1": {},
        "stage2": {},
        "stage3": {},
        "stage4": {},
        "stage5": {},
    }


def export_all_to_csv() -> pd.DataFrame:
    files = sorted(glob.glob(os.path.join(DATA_DIR, "*.json")))
    rows = []
    for fp in files:
        try:
            data = load_analysis(fp)
            row = flatten_dict(data)
            rows.append(row)
        except Exception:
            continue
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows)


def flatten_dict(d: dict, parent_key: str = "", sep: str = ".") -> dict:
    items = {}
    for k, v in d.items():
        new_key = f"{parent_key}{sep}{k}" if parent_key else k
        if isinstance(v, dict):
            items.update(flatten_dict(v, new_key, sep))
        elif isinstance(v, list):
            for i, item in enumerate(v):
                if isinstance(item, dict):
                    items.update(flatten_dict(item, f"{new_key}[{i}]", sep))
                else:
                    items[f"{new_key}[{i}]"] = item
        else:
            items[new_key] = v
    return items


def export_single_to_json(data: dict) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


# ============================================================
# サイドバー：ナビゲーション
# ============================================================

st.sidebar.title("📊 米国株 企業分析ツール")

menu = st.sidebar.radio(
    "メニュー",
    ["新規分析", "分析一覧・再開", "データエクスポート"],
)

# ============================================================
# 新規分析
# ============================================================

if menu == "新規分析":
    st.title("新規 企業分析")

    if "analysis" not in st.session_state:
        st.session_state.analysis = init_analysis_data()

    data = st.session_state.analysis

    # ヘッダー情報
    col1, col2, col3 = st.columns(3)
    with col1:
        data["company_name"] = st.text_input("企業名", value=data.get("company_name", ""))
    with col2:
        data["ticker"] = st.text_input("ティッカー", value=data.get("ticker", "")).upper()
    with col3:
        data["date"] = st.date_input(
            "分析日",
            value=datetime.strptime(data["date"], "%Y-%m-%d") if isinstance(data["date"], str) else data["date"],
        ).strftime("%Y-%m-%d")

    current_stage = data.get("current_stage", 1)

    # タブで各STAGEを表示
    tabs = st.tabs([
        "STAGE 1: スクリーニング",
        "STAGE 2: 定量分析",
        "STAGE 3: 定性分析",
        "STAGE 4: クーリングオフ",
        "STAGE 5: 最終判断",
    ])

    # ==========================================================
    # STAGE 1：5分間スクリーニング
    # ==========================================================
    with tabs[0]:
        st.header("STAGE 1：5分間スクリーニング（GO / NO GO 判定）")
        s1 = data.setdefault("stage1", {})

        col1, col2, col3 = st.columns(3)
        with col1:
            s1["market_cap"] = st.number_input(
                "時価総額（億ドル）", min_value=0.0, value=float(s1.get("market_cap", 0)), step=1.0, key="s1_mcap"
            )
        with col2:
            s1["years_listed"] = st.number_input(
                "上場年数", min_value=0, value=int(s1.get("years_listed", 0)), step=1, key="s1_years"
            )
        with col3:
            s1["sector"] = st.text_input("セクター", value=s1.get("sector", ""), key="s1_sector")

        st.subheader("即座にNO GOとなる条件")
        nogo_items = [
            ("deficit_3years", "赤字が3年以上続いている"),
            ("stock_drop_70", "過去1年で株価が70%以上下落（特別な理由なし）"),
            ("management_scandal", "経営陣の不祥事がある"),
            ("unclear_business", "自分の理解できないビジネスモデル"),
            ("speculative", "レバレッジETF・投機的銘柄"),
        ]
        s1.setdefault("nogo_flags", {})
        for key, label in nogo_items:
            s1["nogo_flags"][key] = st.checkbox(
                label, value=s1["nogo_flags"].get(key, False), key=f"s1_nogo_{key}"
            )

        has_nogo = any(s1["nogo_flags"].values())
        if has_nogo:
            st.error("⚠️ NO GO条件に該当しています。この銘柄は見送りです。")

        st.subheader("GO判定の最低条件")
        go_items = [
            ("explainable", "ビジネスモデルを1文で説明できる"),
            ("future_need", "今後も必要とされる事業である"),
            ("financial_ok", "財務状況が極端に悪くない"),
        ]
        s1.setdefault("go_flags", {})
        for key, label in go_items:
            s1["go_flags"][key] = st.checkbox(
                label, value=s1["go_flags"].get(key, False), key=f"s1_go_{key}"
            )

        all_go = all(s1["go_flags"].values())

        st.divider()
        col_left, col_right = st.columns(2)
        with col_left:
            if not has_nogo and all_go:
                st.success("✅ GO判定 → STAGE 2へ進めます")
                s1["result"] = "GO"
            elif has_nogo:
                st.error("❌ NO GO → この銘柄は見送り")
                s1["result"] = "NO GO"
            else:
                st.warning("GO条件が未達です。すべてチェックしてください。")
                s1["result"] = "未完了"

        with col_right:
            if st.button("STAGE 1を保存", key="save_s1"):
                if s1["result"] == "GO":
                    data["current_stage"] = max(current_stage, 2)
                save_analysis(data)
                st.success("保存しました！")

    # ==========================================================
    # STAGE 2：30分間 定量分析
    # ==========================================================
    with tabs[1]:
        st.header("STAGE 2：30分間 定量分析（スコアリング形式）")
        s2 = data.setdefault("stage2", {})

        # --- A. 成長性分析 ---
        st.subheader("A. 成長性分析")
        with st.expander("売上高の推移", expanded=True):
            col1, col2 = st.columns(2)
            with col1:
                s2["revenue"] = st.number_input(
                    "直近年度売上（億ドル）", value=float(s2.get("revenue", 0)), step=0.1, key="s2_rev"
                )
                s2["revenue_yoy"] = st.number_input(
                    "前年比成長率（%）", value=float(s2.get("revenue_yoy", 0)), step=0.1, key="s2_rev_yoy"
                )
            with col2:
                s2["revenue_3y_avg"] = st.number_input(
                    "過去3年平均成長率（%）", value=float(s2.get("revenue_3y_avg", 0)), step=0.1, key="s2_rev3y"
                )
                s2["industry_growth"] = st.number_input(
                    "業界平均成長率（%）", value=float(s2.get("industry_growth", 0)), step=0.1, key="s2_indg"
                )

            s2["growth_score"] = st.radio(
                "成長性判定",
                options=[2, 1, 0],
                format_func=lambda x: {
                    2: "業界平均を上回る成長（+2点）",
                    1: "業界平均並み（+1点）",
                    0: "業界平均を下回る（0点）",
                }[x],
                index=[2, 1, 0].index(s2.get("growth_score", 1)),
                key="s2_gscore",
            )
            if s2["growth_score"] == 0:
                s2["growth_reason"] = st.text_input("理由", value=s2.get("growth_reason", ""), key="s2_greason")

        with st.expander("利益の推移", expanded=True):
            col1, col2 = st.columns(2)
            with col1:
                s2["operating_margin"] = st.number_input(
                    "営業利益率（%）直近", value=float(s2.get("operating_margin", 0)), step=0.1, key="s2_opmg"
                )
                s2["operating_margin_3y"] = st.number_input(
                    "営業利益率 過去3年平均（%）", value=float(s2.get("operating_margin_3y", 0)), step=0.1, key="s2_opmg3y"
                )
            with col2:
                s2["net_income"] = st.number_input(
                    "純利益（億ドル）", value=float(s2.get("net_income", 0)), step=0.1, key="s2_ni"
                )
                s2["net_income_yoy"] = st.number_input(
                    "純利益 前年比（%）", value=float(s2.get("net_income_yoy", 0)), step=0.1, key="s2_ni_yoy"
                )
            s2["eps_growth_3y"] = st.number_input(
                "EPS成長率 3年（%）", value=float(s2.get("eps_growth_3y", 0)), step=0.1, key="s2_eps3y"
            )

            s2["profit_score"] = st.radio(
                "利益判定",
                options=[2, 1, 0],
                format_func=lambda x: {
                    2: "利益率が改善傾向（+2点）",
                    1: "横ばい（+1点）",
                    0: "悪化傾向（0点）",
                }[x],
                index=[2, 1, 0].index(s2.get("profit_score", 1)),
                key="s2_pscore",
            )
            if s2["profit_score"] == 0:
                s2["profit_reason"] = st.text_input("理由", value=s2.get("profit_reason", ""), key="s2_preason")

        # --- B. 財務健全性 ---
        st.subheader("B. 財務健全性")
        with st.expander("バランスシート", expanded=True):
            col1, col2 = st.columns(2)
            with col1:
                s2["cash"] = st.number_input(
                    "現金・有価証券（億ドル）", value=float(s2.get("cash", 0)), step=0.1, key="s2_cash"
                )
                s2["total_debt"] = st.number_input(
                    "総負債（億ドル）", value=float(s2.get("total_debt", 0)), step=0.1, key="s2_debt"
                )
            with col2:
                s2["equity_ratio"] = st.number_input(
                    "自己資本比率（%）", value=float(s2.get("equity_ratio", 0)), step=0.1, key="s2_eq"
                )
                net_cash = s2.get("cash", 0) - s2.get("total_debt", 0)
                st.metric("ネットキャッシュ（億ドル）", f"{net_cash:.1f}")
                s2["net_cash"] = net_cash

            s2["balance_score"] = st.radio(
                "財務健全性判定",
                options=[2, 1, 0],
                format_func=lambda x: {
                    2: "実質無借金 or ネットキャッシュ（+2点）",
                    1: "適度な負債レベル（+1点）",
                    0: "負債が重い（0点）",
                }[x],
                index=[2, 1, 0].index(s2.get("balance_score", 1)),
                key="s2_bscore",
            )

        with st.expander("キャッシュフロー", expanded=True):
            col1, col2, col3 = st.columns(3)
            with col1:
                s2["operating_cf"] = st.number_input(
                    "営業CF（億ドル）", value=float(s2.get("operating_cf", 0)), step=0.1, key="s2_ocf"
                )
            with col2:
                s2["free_cf"] = st.number_input(
                    "フリーCF（億ドル）", value=float(s2.get("free_cf", 0)), step=0.1, key="s2_fcf"
                )
            with col3:
                s2["fcf_growth"] = st.number_input(
                    "FCF成長率 前年比（%）", value=float(s2.get("fcf_growth", 0)), step=0.1, key="s2_fcfg"
                )

            s2["cf_score"] = st.radio(
                "キャッシュフロー判定",
                options=[2, 1, 0],
                format_func=lambda x: {
                    2: "FCFが安定的に増加（+2点）",
                    1: "FCFはプラス（+1点）",
                    0: "FCFがマイナス（0点）",
                }[x],
                index=[2, 1, 0].index(s2.get("cf_score", 1)),
                key="s2_cfscore",
            )
            if s2["cf_score"] == 0:
                s2["cf_reason"] = st.text_input("理由", value=s2.get("cf_reason", ""), key="s2_cfreason")

        # --- C. バリュエーション ---
        st.subheader("C. バリュエーション")
        with st.expander("バリュエーション指標", expanded=True):
            col1, col2 = st.columns(2)
            with col1:
                s2["stock_price"] = st.number_input(
                    "現在株価（ドル）", value=float(s2.get("stock_price", 0)), step=0.01, key="s2_price"
                )
                s2["per"] = st.number_input(
                    "PER", value=float(s2.get("per", 0)), step=0.1, key="s2_per"
                )
                s2["per_sector_avg"] = st.number_input(
                    "PER セクター平均", value=float(s2.get("per_sector_avg", 0)), step=0.1, key="s2_per_sa"
                )
            with col2:
                s2["psr"] = st.number_input(
                    "PSR", value=float(s2.get("psr", 0)), step=0.1, key="s2_psr"
                )
                s2["psr_sector_avg"] = st.number_input(
                    "PSR セクター平均", value=float(s2.get("psr_sector_avg", 0)), step=0.1, key="s2_psr_sa"
                )
                s2["peg"] = st.number_input(
                    "PEG Ratio（1.0未満が理想）", value=float(s2.get("peg", 0)), step=0.01, key="s2_peg"
                )

            s2["valuation_score"] = st.radio(
                "バリュエーション判定",
                options=[2, 1, 0],
                format_func=lambda x: {
                    2: "割安（セクター平均より20%以上低い）（+2点）",
                    1: "適正（±20%以内）（+1点）",
                    0: "割高（セクター平均より20%以上高い）（0点）",
                }[x],
                index=[2, 1, 0].index(s2.get("valuation_score", 1)),
                key="s2_vscore",
            )

        # 合計スコア
        total = (
            s2.get("growth_score", 0) +
            s2.get("profit_score", 0) +
            s2.get("balance_score", 0) +
            s2.get("cf_score", 0) +
            s2.get("valuation_score", 0)
        )
        s2["total_score"] = total

        st.divider()
        st.subheader(f"定量分析 合計スコア：{total} / 10点")

        if total >= 8:
            st.success("🟢 8-10点：優良。詳細調査の価値あり → STAGE 3へ")
            s2["result"] = "優良"
        elif total >= 5:
            st.warning("🟡 5-7点：標準的。慎重に検討 → STAGE 3へ（確信度は下げる）")
            s2["result"] = "標準"
        else:
            st.error("🔴 0-4点：投資見送り → 分析終了")
            s2["result"] = "見送り"

        if st.button("STAGE 2を保存", key="save_s2"):
            if total >= 5:
                data["current_stage"] = max(current_stage, 3)
            save_analysis(data)
            st.success("保存しました！")

    # ==========================================================
    # STAGE 3：30分間 定性分析＋バイアス除去
    # ==========================================================
    with tabs[2]:
        st.header("STAGE 3：30分間 定性分析＋バイアス除去")
        s3 = data.setdefault("stage3", {})

        # --- A. ビジネス理解 ---
        st.subheader("A. ビジネス理解")

        with st.expander("1. 収益源の理解", expanded=True):
            s3["business_summary"] = st.text_area(
                "この会社は何で稼いでいるか？（1文で）",
                value=s3.get("business_summary", ""),
                key="s3_bsummary",
            )
            col1, col2, col3 = st.columns(3)
            with col1:
                s3["revenue_source_a_name"] = st.text_input(
                    "製品A/サービスA 名称", value=s3.get("revenue_source_a_name", ""), key="s3_rsa_name"
                )
                s3["revenue_source_a_pct"] = st.number_input(
                    "割合（%）", value=float(s3.get("revenue_source_a_pct", 0)), step=1.0, key="s3_rsa_pct"
                )
            with col2:
                s3["revenue_source_b_name"] = st.text_input(
                    "製品B/サービスB 名称", value=s3.get("revenue_source_b_name", ""), key="s3_rsb_name"
                )
                s3["revenue_source_b_pct"] = st.number_input(
                    "割合（%）", value=float(s3.get("revenue_source_b_pct", 0)), step=1.0, key="s3_rsb_pct"
                )
            with col3:
                s3["revenue_source_other_pct"] = st.number_input(
                    "その他（%）", value=float(s3.get("revenue_source_other_pct", 0)), step=1.0, key="s3_rso_pct"
                )

        with st.expander("2. 競争優位性（モート）の確認", expanded=True):
            moat_items = [
                ("network_effect", "ネットワーク効果（例：Meta, Visa）"),
                ("brand", "ブランド力（例：Apple, Nike）"),
                ("cost_advantage", "コスト優位性（例：Walmart）"),
                ("regulatory_barrier", "規制参入障壁（例：製薬会社）"),
                ("switching_cost", "スイッチングコスト（例：Microsoft, Salesforce）"),
                ("patent_tech", "特許・技術優位性"),
            ]
            s3.setdefault("moat", {})
            for key, label in moat_items:
                s3["moat"][key] = st.checkbox(
                    label, value=s3["moat"].get(key, False), key=f"s3_moat_{key}"
                )
            s3["moat_other"] = st.text_input(
                "その他の優位性", value=s3.get("moat_other", ""), key="s3_moat_other"
            )

            moat_count = sum(1 for v in s3["moat"].values() if v) + (1 if s3["moat_other"] else 0)
            if moat_count == 0:
                st.warning("⚠️ 競争優位性が0個 → 投資リスク大（確信度を下げる）")

        with st.expander("3. 経営陣の評価", expanded=True):
            col1, col2 = st.columns(2)
            with col1:
                s3["ceo_name"] = st.text_input("CEO名", value=s3.get("ceo_name", ""), key="s3_ceo")
                s3["ceo_tenure"] = st.number_input(
                    "在任期間（年）", min_value=0, value=int(s3.get("ceo_tenure", 0)), step=1, key="s3_ceo_tenure"
                )
            with col2:
                s3["founder_or_long"] = st.checkbox(
                    "創業者 or 長期在任（5年以上）", value=s3.get("founder_or_long", False), key="s3_founder"
                )
                s3["clear_shareholder_policy"] = st.checkbox(
                    "株主への利益還元方針が明確", value=s3.get("clear_shareholder_policy", False), key="s3_shpol"
                )
            s3["insider_ownership"] = st.number_input(
                "インサイダー保有比率（%）（5%以上が望ましい）",
                value=float(s3.get("insider_ownership", 0)), step=0.1, key="s3_insider"
            )
            s3["past_scandal"] = st.radio(
                "過去の不祥事・問題",
                options=["なし", "あり"],
                index=0 if s3.get("past_scandal", "なし") == "なし" else 1,
                key="s3_scandal",
            )
            if s3["past_scandal"] == "あり":
                s3["scandal_detail"] = st.text_input(
                    "内容", value=s3.get("scandal_detail", ""), key="s3_scandal_detail"
                )

        with st.expander("4. 成長戦略の確認", expanded=True):
            st.markdown("今後の成長ドライバーは何か？（3つまで）")
            s3["growth_driver_1"] = st.text_input(
                "成長ドライバー 1", value=s3.get("growth_driver_1", ""), key="s3_gd1"
            )
            s3["growth_driver_2"] = st.text_input(
                "成長ドライバー 2", value=s3.get("growth_driver_2", ""), key="s3_gd2"
            )
            s3["growth_driver_3"] = st.text_input(
                "成長ドライバー 3", value=s3.get("growth_driver_3", ""), key="s3_gd3"
            )
            s3["growth_feasibility"] = st.radio(
                "これらは実現可能か？",
                options=["high", "uncertain", "difficult"],
                format_func=lambda x: {
                    "high": "高い確率で実現できそう",
                    "uncertain": "実現には不確実性がある",
                    "difficult": "実現は困難そう → 投資見送り",
                }[x],
                index=["high", "uncertain", "difficult"].index(
                    s3.get("growth_feasibility", "high")
                ),
                key="s3_gfeas",
            )
            if s3["growth_feasibility"] == "uncertain":
                s3["growth_risk"] = st.text_input(
                    "リスク要因", value=s3.get("growth_risk", ""), key="s3_grisk"
                )

        # --- B. 悪魔の代弁者セクション ---
        st.subheader("B. 🔴 悪魔の代弁者セクション（必須）")
        st.warning("⚠️ このセクションを飛ばした場合、分析は無効となります")

        with st.expander("強制質問1：なぜこの銘柄は「今」買うべきではないのか？（3つ以上）", expanded=True):
            s3["devil_q1_1"] = st.text_input("理由 1", value=s3.get("devil_q1_1", ""), key="s3_dq1_1")
            s3["devil_q1_2"] = st.text_input("理由 2", value=s3.get("devil_q1_2", ""), key="s3_dq1_2")
            s3["devil_q1_3"] = st.text_input("理由 3", value=s3.get("devil_q1_3", ""), key="s3_dq1_3")

        with st.expander("強制質問2：プロの投資家がこの銘柄を売っている理由は？（2つ）", expanded=True):
            s3["devil_q2_1"] = st.text_input("理由 1", value=s3.get("devil_q2_1", ""), key="s3_dq2_1")
            s3["devil_q2_2"] = st.text_input("理由 2", value=s3.get("devil_q2_2", ""), key="s3_dq2_2")

        with st.expander("強制質問3：自分が見落としているリスクは？", expanded=True):
            s3["devil_q3_1"] = st.text_input("リスク 1", value=s3.get("devil_q3_1", ""), key="s3_dq3_1")
            s3["devil_q3_2"] = st.text_input("リスク 2", value=s3.get("devil_q3_2", ""), key="s3_dq3_2")

        with st.expander("強制質問4：この銘柄について、自分は感情的になっていないか？", expanded=True):
            bias_items = [
                ("bias_certain", "「絶対上がる」と思っている → ⚠️危険信号"),
                ("bias_sunk_cost", "「調査に時間をかけたから買わないともったいない」と思っている → ⚠️危険信号"),
                ("bias_past_loss", "過去にこの銘柄で損失を出している → ⚠️危険信号"),
                ("bias_calm", "冷静に判断できている"),
            ]
            s3.setdefault("bias_flags", {})
            for key, label in bias_items:
                s3["bias_flags"][key] = st.checkbox(
                    label, value=s3["bias_flags"].get(key, False), key=f"s3_bias_{key}"
                )

            danger_count = sum(1 for k, v in s3["bias_flags"].items() if v and k != "bias_calm")
            if danger_count > 0:
                st.error(f"🔴 危険信号が{danger_count}個あります → 確信度を2段階下げる or 投資見送り")

        # バリデーション
        devil_complete = all([
            s3.get("devil_q1_1"), s3.get("devil_q1_2"), s3.get("devil_q1_3"),
            s3.get("devil_q2_1"), s3.get("devil_q2_2"),
            s3.get("devil_q3_1"), s3.get("devil_q3_2"),
        ])

        st.divider()
        if not devil_complete:
            st.warning("悪魔の代弁者セクションがすべて入力されていません。分析は無効です。")
            s3["result"] = "未完了"
        else:
            st.success("✅ 悪魔の代弁者セクション完了 → STAGE 4へ進めます")
            s3["result"] = "完了"

        if st.button("STAGE 3を保存", key="save_s3"):
            if devil_complete:
                data["current_stage"] = max(current_stage, 4)
            save_analysis(data)
            st.success("保存しました！")

    # ==========================================================
    # STAGE 4：24時間待機（クーリングオフ期間）
    # ==========================================================
    with tabs[3]:
        st.header("STAGE 4：24時間待機（クーリングオフ期間）")
        s4 = data.setdefault("stage4", {})

        col1, col2 = st.columns(2)
        with col1:
            analysis_date_val = s4.get("analysis_complete_date", "")
            if analysis_date_val:
                try:
                    default_date = datetime.strptime(analysis_date_val, "%Y-%m-%d")
                except (ValueError, TypeError):
                    default_date = datetime.now()
            else:
                default_date = datetime.now()
            s4["analysis_complete_date"] = st.date_input(
                "分析完了日", value=default_date, key="s4_comp_date"
            ).strftime("%Y-%m-%d")
        with col2:
            review_default = default_date + timedelta(days=1)
            review_date_val = s4.get("review_date", "")
            if review_date_val:
                try:
                    review_default = datetime.strptime(review_date_val, "%Y-%m-%d")
                except (ValueError, TypeError):
                    pass
            s4["review_date"] = st.date_input(
                "再検討日（最低24時間後）", value=review_default, key="s4_review_date"
            ).strftime("%Y-%m-%d")

        # 24時間チェック
        try:
            comp_dt = datetime.strptime(s4["analysis_complete_date"], "%Y-%m-%d")
            rev_dt = datetime.strptime(s4["review_date"], "%Y-%m-%d")
            hours_diff = (rev_dt - comp_dt).total_seconds() / 3600
            if hours_diff < 24:
                st.error("⚠️ 再検討日は分析完了日から最低24時間後にしてください")
                s4["cooling_ok"] = False
            else:
                st.success(f"✅ 待機期間：{hours_diff:.0f}時間（24時間以上）")
                s4["cooling_ok"] = True
        except (ValueError, KeyError):
            s4["cooling_ok"] = False

        st.subheader("待機中の禁止事項")
        prohibitions = [
            ("no_price_check", "この銘柄の株価を見ない"),
            ("no_positive_info", "追加のポジティブ情報を集めない"),
            ("no_social_media", "Twitterや掲示板を見ない"),
        ]
        s4.setdefault("prohibitions", {})
        for key, label in prohibitions:
            s4["prohibitions"][key] = st.checkbox(
                label, value=s4["prohibitions"].get(key, False), key=f"s4_proh_{key}"
            )

        st.subheader("待機中にやること")
        todos = [
            ("compare_others", "他の銘柄も2-3社分析して比較検討"),
            ("review_portfolio", "自分のポートフォリオ全体を見直す"),
            ("reflect_emotions", "なぜこの銘柄が気になるのか、感情を整理する"),
        ]
        s4.setdefault("todos", {})
        for key, label in todos:
            s4["todos"][key] = st.checkbox(
                label, value=s4["todos"].get(key, False), key=f"s4_todo_{key}"
            )

        st.divider()
        all_prohibitions = all(s4["prohibitions"].values())
        all_todos = all(s4["todos"].values())

        if s4.get("cooling_ok") and all_prohibitions and all_todos:
            st.success("✅ クーリングオフ完了 → STAGE 5へ進めます")
            s4["result"] = "完了"
        else:
            remaining = []
            if not s4.get("cooling_ok"):
                remaining.append("24時間以上の待機")
            if not all_prohibitions:
                remaining.append("禁止事項の遵守")
            if not all_todos:
                remaining.append("待機中タスクの実施")
            st.info(f"残りの確認項目：{', '.join(remaining)}")
            s4["result"] = "未完了"

        if st.button("STAGE 4を保存", key="save_s4"):
            if s4.get("result") == "完了":
                data["current_stage"] = max(current_stage, 5)
            save_analysis(data)
            st.success("保存しました！")

    # ==========================================================
    # STAGE 5：最終判断
    # ==========================================================
    with tabs[4]:
        st.header("STAGE 5：最終判断（投資 or パス）")
        st.markdown("24時間後、冷静な状態で再評価")
        s5 = data.setdefault("stage5", {})

        st.subheader("最終チェックリスト")
        final_checks = [
            ("score_5plus", f"STAGE 2のスコアが5点以上（現在：{data.get('stage2', {}).get('total_score', '未計算')}点）"),
            ("devil_done", "STAGE 3の悪魔の代弁者セクションを完了した"),
            ("24h_passed", "24時間以上経過した"),
            ("still_want", "今でもまだこの銘柄を買いたいと思う"),
            ("best_choice", "他の選択肢と比較しても、これがベストだと言える"),
        ]
        s5.setdefault("final_checks", {})
        for key, label in final_checks:
            s5["final_checks"][key] = st.checkbox(
                label, value=s5["final_checks"].get(key, False), key=f"s5_fc_{key}"
            )

        st.subheader("投資判断")
        s5["decision"] = st.radio(
            "判断",
            options=["invest", "watchlist", "pass"],
            format_func=lambda x: {
                "invest": "投資する",
                "watchlist": "ウォッチリストに入れる（時期を待つ）",
                "pass": "パス（投資しない）",
            }[x],
            index=["invest", "watchlist", "pass"].index(s5.get("decision", "pass")),
            key="s5_decision",
        )

        if s5["decision"] == "invest":
            st.subheader("確信度とポジションサイズ")
            st.markdown("""
| 確信度 | ポジションサイズ | 備考 |
|--------|-----------------|------|
| **最高** | **投資禁止** | バイアスリスク大 |
| 高 | 標準（5-8%） | |
| 普通 | 小さめ（3-5%） | |
| 低 | 最小ロット（1-3%） | |
            """)

            s5["conviction"] = st.select_slider(
                "確信度",
                options=["低", "普通", "高", "最高"],
                value=s5.get("conviction", "普通"),
                key="s5_conv",
            )

            if s5["conviction"] == "最高":
                st.error("🔴 確信度「最高」= 投資禁止（バイアスリスク大）")

            conviction_to_size = {"低": "1-3%", "普通": "3-5%", "高": "5-8%", "最高": "投資禁止"}
            recommended_size = conviction_to_size[s5["conviction"]]
            st.info(f"推奨ポジションサイズ：ポートフォリオの {recommended_size}")

            s5["position_size"] = st.text_input(
                "ポジションサイズ", value=s5.get("position_size", recommended_size), key="s5_size"
            )

            st.subheader("投資記録")
            s5["buy_reason"] = st.text_area(
                "購入理由（3行以内）", value=s5.get("buy_reason", ""), max_chars=300, key="s5_reason"
            )
            col1, col2 = st.columns(2)
            with col1:
                s5["target_price"] = st.number_input(
                    "目標株価（ドル）", value=float(s5.get("target_price", 0)), step=0.01, key="s5_target"
                )
                s5["target_basis"] = st.text_input(
                    "目標株価の根拠", value=s5.get("target_basis", ""), key="s5_target_basis"
                )
            with col2:
                s5["max_loss_pct"] = st.number_input(
                    "最大許容損失（%）（この水準で必ず損切り）",
                    value=float(s5.get("max_loss_pct", 0)), step=0.1, key="s5_maxloss"
                )
                review_default_5 = (datetime.now() + timedelta(days=180)).strftime("%Y-%m-%d")
                review_val_5 = s5.get("review_date", review_default_5)
                try:
                    review_dt_5 = datetime.strptime(review_val_5, "%Y-%m-%d")
                except (ValueError, TypeError):
                    review_dt_5 = datetime.now() + timedelta(days=180)
                s5["review_date"] = st.date_input(
                    "見直し時期（6ヶ月後推奨）", value=review_dt_5, key="s5_review"
                ).strftime("%Y-%m-%d")

        st.divider()
        if st.button("STAGE 5を保存（分析完了）", key="save_s5"):
            data["current_stage"] = 5
            save_analysis(data)
            st.success("分析を保存しました！")
            st.balloons()

    # 全STAGE共通の保存ボタン
    st.sidebar.divider()
    if st.sidebar.button("現在の分析を一括保存"):
        save_analysis(data)
        st.sidebar.success("保存しました！")

    if st.sidebar.button("新規分析をリセット"):
        st.session_state.analysis = init_analysis_data()
        st.rerun()


# ============================================================
# 分析一覧・再開
# ============================================================

elif menu == "分析一覧・再開":
    st.title("分析一覧")

    analyses = list_analyses()

    if not analyses:
        st.info("保存された分析データがありません。「新規分析」から始めてください。")
    else:
        # 一覧テーブル
        df = pd.DataFrame(analyses)
        df_display = df[["ticker", "company", "date", "current_stage", "stage1_result", "stage2_score", "stage5_decision"]]
        df_display.columns = ["ティッカー", "企業名", "分析日", "進捗STAGE", "STAGE1判定", "STAGE2スコア", "最終判断"]

        st.dataframe(df_display, use_container_width=True, hide_index=True)

        # 分析の再開
        st.subheader("分析を再開する")
        options = [f"{a['ticker']} - {a['company']} ({a['date']})" for a in analyses]
        selected = st.selectbox("再開する分析を選択", options)

        if selected and st.button("この分析を再開"):
            idx = options.index(selected)
            filepath = analyses[idx]["filepath"]
            st.session_state.analysis = load_analysis(filepath)
            st.sidebar.write("")  # Force sidebar re-render
            st.info("分析データを読み込みました。サイドバーで「新規分析」を選択して編集を続けてください。")

        # 削除機能
        st.subheader("分析を削除する")
        selected_del = st.selectbox("削除する分析を選択", options, key="del_select")
        if selected_del and st.button("この分析を削除", type="secondary"):
            idx = options.index(selected_del)
            filepath = analyses[idx]["filepath"]
            os.remove(filepath)
            st.success("削除しました。")
            st.rerun()


# ============================================================
# データエクスポート
# ============================================================

elif menu == "データエクスポート":
    st.title("データエクスポート")

    analyses = list_analyses()

    if not analyses:
        st.info("エクスポートするデータがありません。")
    else:
        st.subheader("全データ一括エクスポート")

        col1, col2 = st.columns(2)

        with col1:
            st.markdown("### CSV形式")
            csv_df = export_all_to_csv()
            if not csv_df.empty:
                csv_data = csv_df.to_csv(index=False).encode("utf-8-sig")
                st.download_button(
                    label="📥 全データをCSVでダウンロード",
                    data=csv_data,
                    file_name=f"stock_analyses_{datetime.now().strftime('%Y%m%d')}.csv",
                    mime="text/csv",
                )

        with col2:
            st.markdown("### JSON形式")
            all_data = []
            for a in analyses:
                try:
                    all_data.append(load_analysis(a["filepath"]))
                except Exception:
                    continue
            json_data = json.dumps(all_data, ensure_ascii=False, indent=2).encode("utf-8")
            st.download_button(
                label="📥 全データをJSONでダウンロード",
                data=json_data,
                file_name=f"stock_analyses_{datetime.now().strftime('%Y%m%d')}.json",
                mime="application/json",
            )

        st.divider()
        st.subheader("個別分析のエクスポート")

        options = [f"{a['ticker']} - {a['company']} ({a['date']})" for a in analyses]
        selected = st.selectbox("エクスポートする分析を選択", options, key="export_select")

        if selected:
            idx = options.index(selected)
            filepath = analyses[idx]["filepath"]
            single_data = load_analysis(filepath)

            col1, col2 = st.columns(2)
            with col1:
                single_json = json.dumps(single_data, ensure_ascii=False, indent=2).encode("utf-8")
                st.download_button(
                    label="📥 JSONでダウンロード",
                    data=single_json,
                    file_name=f"{single_data.get('ticker', 'analysis')}_{single_data.get('date', 'unknown')}.json",
                    mime="application/json",
                    key="dl_single_json",
                )

            with col2:
                single_df = pd.DataFrame([flatten_dict(single_data)])
                single_csv = single_df.to_csv(index=False).encode("utf-8-sig")
                st.download_button(
                    label="📥 CSVでダウンロード",
                    data=single_csv,
                    file_name=f"{single_data.get('ticker', 'analysis')}_{single_data.get('date', 'unknown')}.csv",
                    mime="text/csv",
                    key="dl_single_csv",
                )

            # プレビュー
            with st.expander("データプレビュー（JSON）"):
                st.json(single_data)
