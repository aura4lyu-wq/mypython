#!/usr/bin/env python3
"""
PDF VOICEVOX Reader - GUI版

VOICEVOX自動起動 + PDFファイル選択UI付き読み上げアプリ

使い方:
  python pdf_voicevox_gui.py
  python pdf_voicevox_gui.py earnings_call.pdf   # 起動時にPDFを指定
"""

import sys
import io
import re
import os
import glob
import time
import queue
import shutil
import subprocess
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext
from pathlib import Path

import requests
import fitz       # PyMuPDF
import pygame

# ── 定数 ─────────────────────────────────────────────────────────────────────

VOICEVOX_DEFAULT_URL = "http://localhost:50021"

# VOICEVOX 実行ファイルの検索パス候補（Linux / Mac / Windows WSL）
def _voicevox_candidates() -> list[Path]:
    cands: list[Path] = [
        # Linux: 展開版
        Path.home() / "VOICEVOX" / "run",
        Path.home() / "VOICEVOX" / "voicevox",
        Path("/opt/VOICEVOX/run"),
        Path("/opt/VOICEVOX/voicevox"),
        # Linux: engine standalone
        Path.home() / "voicevox_engine" / "run",
        Path.home() / "voicevox-engine" / "run",
        Path("/opt/voicevox_engine/run"),
        # macOS
        Path("/Applications/VOICEVOX.app/Contents/MacOS/VOICEVOX"),
        # Windows (WSL)
        Path("/mnt/c/Program Files/VOICEVOX/VOICEVOX.exe"),
    ]
    # AppImage（ホームや /opt 直下）
    for pattern in [
        str(Path.home() / "VOICEVOX*.AppImage"),
        str(Path.home() / "voicevox*.AppImage"),
        str(Path.home() / "VOICEVOX" / "*.AppImage"),
        "/opt/VOICEVOX*.AppImage",
        "/opt/voicevox*.AppImage",
    ]:
        cands.extend(Path(p) for p in glob.glob(pattern))
    return cands


# ── PDF ユーティリティ ────────────────────────────────────────────────────────

def extract_pages(pdf_path: str) -> list[str]:
    """PDFの各ページからテキスト文字列を抽出してリストで返す"""
    doc = fitz.open(pdf_path)
    pages = [page.get_text() for page in doc]
    doc.close()
    return pages


def clean_text(text: str) -> str:
    """PDF抽出テキストの余分な空白・改行を整理する"""
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r" +\n", "\n", text)
    # 英文PDFのハイフン改行をつなぐ
    text = re.sub(r"-\n([a-z])", r"\1", text)
    return text.strip()


def split_into_chunks(text: str, max_chars: int = 120) -> list[str]:
    """テキストを VOICEVOX が扱いやすい長さのチャンクに分割する"""
    text = clean_text(text)
    if not text:
        return []

    # 句読点・改行で分割
    raw = re.split(r"(?<=[。！？\.\!\?])\s+|(?<=\n)", text)
    chunks: list[str] = []
    current = ""

    for s in raw:
        s = s.strip()
        if not s:
            continue
        if len(current) + len(s) + 1 <= max_chars:
            current = (current + " " + s).strip() if current else s
        else:
            if current:
                chunks.append(current)
            if len(s) > max_chars:
                for i in range(0, len(s), max_chars):
                    chunks.append(s[i : i + max_chars])
                current = ""
            else:
                current = s

    if current:
        chunks.append(current)
    return chunks


# ── VOICEVOX API ──────────────────────────────────────────────────────────────

def voicevox_get_speakers(base_url: str) -> list[dict] | None:
    """話者一覧を取得する。失敗時は None を返す"""
    try:
        resp = requests.get(f"{base_url}/speakers", timeout=5)
        resp.raise_for_status()
        return resp.json()
    except Exception:
        return None


def voicevox_synthesize(
    text: str, speaker_id: int, speed: float, base_url: str
) -> bytes | None:
    """テキストを音声合成して WAV バイト列を返す。失敗時は None"""
    try:
        # Step1: audio_query
        r = requests.post(
            f"{base_url}/audio_query",
            params={"text": text, "speaker": speaker_id},
            timeout=15,
        )
        r.raise_for_status()
        query = r.json()
        query["speedScale"] = speed

        # Step2: synthesis
        r = requests.post(
            f"{base_url}/synthesis",
            params={"speaker": speaker_id},
            json=query,
            timeout=30,
        )
        r.raise_for_status()
        return r.content
    except Exception:
        return None


def find_voicevox_executable() -> Path | None:
    """VOICEVOX の実行ファイルを検索して返す"""
    for path in _voicevox_candidates():
        if path.exists() and os.access(path, os.X_OK):
            return path
    # PATH から探す
    for name in ("voicevox", "voicevox_engine"):
        found = shutil.which(name)
        if found:
            return Path(found)
    return None


# ── GUI アプリ ────────────────────────────────────────────────────────────────

class App(tk.Tk):
    """PDF VOICEVOX Reader メインウィンドウ"""

    def __init__(self, initial_pdf: str | None = None):
        super().__init__()
        self.title("PDF VOICEVOX Reader")
        self.geometry("740x700")
        self.resizable(True, True)
        self.minsize(620, 600)

        # ── 状態変数 ──────────────────────────────────────────
        self.voicevox_url: str = VOICEVOX_DEFAULT_URL
        self.voicevox_proc: subprocess.Popen | None = None
        self.reader_thread: threading.Thread | None = None
        self.stop_event = threading.Event()
        self.pause_event = threading.Event()   # set = 一時停止中
        self.log_queue: queue.Queue = queue.Queue()
        self.status_queue: queue.Queue = queue.Queue()
        self.speaker_list: list[dict] = []     # [{id, label}, ...]
        self.total_pages: int = 0

        # ── Tk 変数 ────────────────────────────────────────────
        self.pdf_path_var = tk.StringVar()
        self.speaker_var = tk.StringVar(value="VOICEVOX 起動後に読み込みます...")
        self.speed_var = tk.DoubleVar(value=1.0)
        self.start_page_var = tk.IntVar(value=1)
        self.voicevox_exe_var = tk.StringVar()

        # ── pygame 初期化 ──────────────────────────────────────
        pygame.mixer.init(frequency=24000, size=-16, channels=1, buffer=512)

        # ── UI 構築 ────────────────────────────────────────────
        self._apply_style()
        self._build_ui()

        # ── イベントループへのポーリング開始 ───────────────────
        self._poll()

        # ── VOICEVOX 自動起動 ──────────────────────────────────
        threading.Thread(target=self._launch_voicevox, daemon=True).start()

        # ── 起動時 PDF 指定 ────────────────────────────────────
        if initial_pdf:
            self.after(200, lambda: self._load_pdf(initial_pdf))

        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ─────────────────────────────────────────────────────────────────────────
    # スタイル
    # ─────────────────────────────────────────────────────────────────────────

    def _apply_style(self):
        s = ttk.Style(self)
        s.theme_use("clam")
        s.configure("TButton",   padding=(8, 5))
        s.configure("TLabel",    padding=2)
        s.configure("TLabelframe.Label", font=("TkDefaultFont", 9, "bold"))
        s.configure("Play.TButton",  foreground="#ffffff", background="#0070c0",
                    font=("TkDefaultFont", 10, "bold"))
        s.configure("Stop.TButton",  foreground="#ffffff", background="#c00000")
        s.configure("Pause.TButton", foreground="#ffffff", background="#7030a0")
        s.map("Play.TButton",  background=[("active", "#005a9e")])
        s.map("Stop.TButton",  background=[("active", "#9b0000")])
        s.map("Pause.TButton", background=[("active", "#5a2080")])

    # ─────────────────────────────────────────────────────────────────────────
    # UI 構築
    # ─────────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        PAD = {"padx": 10, "pady": 5}

        # ── VOICEVOX エンジン ──────────────────────────────────
        vox_frame = ttk.LabelFrame(self, text="VOICEVOX エンジン")
        vox_frame.pack(fill=tk.X, **PAD)

        self.vox_status_dot = ttk.Label(vox_frame, text="●", foreground="orange",
                                        font=("TkDefaultFont", 12))
        self.vox_status_dot.pack(side=tk.LEFT, padx=(8, 2), pady=5)
        self.vox_status_msg = ttk.Label(vox_frame, text="起動中...")
        self.vox_status_msg.pack(side=tk.LEFT, pady=5)

        ttk.Button(vox_frame, text="実行ファイルを指定...",
                   command=self._pick_voicevox_exe).pack(side=tk.RIGHT, padx=8, pady=5)

        # ── PDF ファイル ───────────────────────────────────────
        pdf_frame = ttk.LabelFrame(self, text="PDF ファイル")
        pdf_frame.pack(fill=tk.X, **PAD)
        pdf_frame.columnconfigure(0, weight=1)

        ttk.Entry(pdf_frame, textvariable=self.pdf_path_var,
                  state="readonly").grid(row=0, column=0, sticky=tk.EW,
                                         padx=(8, 4), pady=6)
        ttk.Button(pdf_frame, text="📂  参照...",
                   command=self._pick_pdf).grid(row=0, column=1, padx=(4, 8), pady=6)

        # ── 設定 ───────────────────────────────────────────────
        cfg_frame = ttk.LabelFrame(self, text="読み上げ設定")
        cfg_frame.pack(fill=tk.X, **PAD)
        cfg_frame.columnconfigure(1, weight=1)

        # 話者
        ttk.Label(cfg_frame, text="話者:").grid(
            row=0, column=0, sticky=tk.W, padx=(10, 4), pady=6)
        self.speaker_cb = ttk.Combobox(
            cfg_frame, textvariable=self.speaker_var,
            state="disabled", width=30)
        self.speaker_cb.grid(row=0, column=1, sticky=tk.EW,
                              padx=(0, 10), pady=6)

        # 開始ページ
        ttk.Label(cfg_frame, text="開始ページ:").grid(
            row=0, column=2, sticky=tk.W, padx=(10, 4), pady=6)
        ttk.Spinbox(cfg_frame, from_=1, to=9999,
                    textvariable=self.start_page_var, width=6).grid(
            row=0, column=3, sticky=tk.W, padx=(0, 4), pady=6)
        self.page_total_lbl = ttk.Label(cfg_frame, text="/ --")
        self.page_total_lbl.grid(row=0, column=4, sticky=tk.W,
                                  padx=(0, 10), pady=6)

        # 速度スライダー
        ttk.Label(cfg_frame, text="速度:").grid(
            row=1, column=0, sticky=tk.W, padx=(10, 4), pady=6)
        spd_inner = ttk.Frame(cfg_frame)
        spd_inner.grid(row=1, column=1, columnspan=4, sticky=tk.EW,
                       padx=(0, 10), pady=4)
        ttk.Label(spd_inner, text="0.5×").pack(side=tk.LEFT)
        ttk.Scale(spd_inner, from_=0.5, to=2.0, variable=self.speed_var,
                  orient=tk.HORIZONTAL,
                  command=self._on_speed_change).pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=6)
        ttk.Label(spd_inner, text="2.0×").pack(side=tk.LEFT)
        self.speed_lbl = ttk.Label(spd_inner, text="1.0×", width=5)
        self.speed_lbl.pack(side=tk.LEFT, padx=(8, 0))

        # ── 操作ボタン ─────────────────────────────────────────
        btn_frame = ttk.Frame(self)
        btn_frame.pack(fill=tk.X, padx=10, pady=(6, 2))

        self.start_btn = ttk.Button(
            btn_frame, text="▶  読み上げ開始",
            style="Play.TButton", command=self._start_reading, state="disabled")
        self.start_btn.pack(side=tk.LEFT, padx=(0, 6))

        self.pause_btn = ttk.Button(
            btn_frame, text="⏸  一時停止",
            style="Pause.TButton", command=self._toggle_pause, state="disabled")
        self.pause_btn.pack(side=tk.LEFT, padx=(0, 6))

        self.stop_btn = ttk.Button(
            btn_frame, text="⏹  停止",
            style="Stop.TButton", command=self._stop_reading, state="disabled")
        self.stop_btn.pack(side=tk.LEFT)

        # ── 進捗 ───────────────────────────────────────────────
        prog_frame = ttk.LabelFrame(self, text="進捗")
        prog_frame.pack(fill=tk.X, **PAD)

        self.prog_page_lbl = ttk.Label(prog_frame, text="待機中")
        self.prog_page_lbl.pack(anchor=tk.W, padx=8, pady=(4, 2))

        self.prog_bar = ttk.Progressbar(prog_frame, mode="determinate",
                                         maximum=100, length=300)
        self.prog_bar.pack(fill=tk.X, padx=8, pady=2)

        self.prog_text_lbl = ttk.Label(
            prog_frame, text="", wraplength=600,
            foreground="#555555", font=("TkDefaultFont", 9))
        self.prog_text_lbl.pack(anchor=tk.W, padx=8, pady=(2, 6))

        # ── ログ ───────────────────────────────────────────────
        log_frame = ttk.LabelFrame(self, text="ログ")
        log_frame.pack(fill=tk.BOTH, expand=True, **PAD)

        self.log_box = scrolledtext.ScrolledText(
            log_frame, height=10, state="disabled",
            font=("Courier", 9), bg="#1e1e1e", fg="#d4d4d4",
            relief=tk.FLAT)
        self.log_box.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

        # ログ色タグ
        self.log_box.tag_config("info",  foreground="#9cdcfe")
        self.log_box.tag_config("ok",    foreground="#4ec9b0")
        self.log_box.tag_config("error", foreground="#f44747")
        self.log_box.tag_config("read",  foreground="#dcdcaa")
        self.log_box.tag_config("chunk", foreground="#ce9178")

    # ─────────────────────────────────────────────────────────────────────────
    # イベントハンドラ（UI スレッド）
    # ─────────────────────────────────────────────────────────────────────────

    def _on_speed_change(self, _=None):
        v = round(self.speed_var.get() * 10) / 10   # 0.1刻み
        self.speed_var.set(v)
        self.speed_lbl.config(text=f"{v:.1f}×")

    def _pick_pdf(self):
        path = filedialog.askopenfilename(
            title="PDF ファイルを選択",
            filetypes=[("PDF ファイル", "*.pdf"), ("すべて", "*.*")],
        )
        if path:
            self._load_pdf(path)

    def _load_pdf(self, path: str):
        try:
            doc = fitz.open(path)
            n = len(doc)
            doc.close()
        except Exception as e:
            messagebox.showerror("エラー", f"PDF を開けませんでした:\n{e}")
            return
        self.pdf_path_var.set(path)
        self.total_pages = n
        self.page_total_lbl.config(text=f"/ {n}")
        self.start_page_var.set(1)
        self._log("info", f"PDF: {Path(path).name}  ({n} ページ)")
        self._refresh_start_btn()

    def _pick_voicevox_exe(self):
        path = filedialog.askopenfilename(
            title="VOICEVOX の実行ファイルを選択",
            filetypes=[("実行ファイル", "run voicevox* *.AppImage *.exe"),
                       ("すべて", "*.*")],
        )
        if not path:
            return
        self.voicevox_exe_var.set(path)
        self._log("info", f"VOICEVOX パスを設定: {path}")
        self._set_vox_status("orange", "再接続中...")
        threading.Thread(target=self._launch_voicevox, daemon=True).start()

    def _refresh_start_btn(self):
        ok = bool(self.pdf_path_var.get()) and bool(self.speaker_list)
        self.start_btn.config(state="normal" if ok else "disabled")

    # ─────────────────────────────────────────────────────────────────────────
    # ログ出力（UI スレッドのみから呼ぶ）
    # ─────────────────────────────────────────────────────────────────────────

    def _log(self, level: str, msg: str):
        self.log_box.config(state="normal")
        self.log_box.insert(tk.END, f"  {msg}\n", level)
        self.log_box.see(tk.END)
        self.log_box.config(state="disabled")

    # ─────────────────────────────────────────────────────────────────────────
    # キューポーリング（UI スレッド）
    # ─────────────────────────────────────────────────────────────────────────

    def _poll(self):
        try:
            while not self.log_queue.empty():
                level, msg = self.log_queue.get_nowait()
                self._log(level, msg)
            while not self.status_queue.empty():
                self._handle_status(self.status_queue.get_nowait())
        except Exception:
            pass
        self.after(100, self._poll)

    def _handle_status(self, evt: tuple):
        kind = evt[0]

        if kind == "vox_ok":
            spk_data = evt[1]
            self._set_vox_status("green", f"接続中  (話者 {len(spk_data)} 名)")
            self._populate_speakers(spk_data)

        elif kind == "vox_error":
            self._set_vox_status("red", "未接続")

        elif kind == "progress":
            _, page, total, ci, ct, text = evt
            pct = int((page - 1 + ci / max(ct, 1)) / total * 100)
            self.prog_bar["value"] = pct
            self.prog_page_lbl.config(
                text=f"ページ {page} / {total}   チャンク {ci} / {ct}")
            self.prog_text_lbl.config(
                text=text[:110] + ("…" if len(text) > 110 else ""))

        elif kind == "done":
            self.prog_page_lbl.config(text="読み上げ完了！")
            self.prog_text_lbl.config(text="")
            self.prog_bar["value"] = 100
            self._set_buttons_idle()

        elif kind == "stopped":
            self.prog_page_lbl.config(text="停止しました")
            self.prog_text_lbl.config(text="")
            self._set_buttons_idle()

    def _set_vox_status(self, color: str, msg: str):
        self.vox_status_dot.config(foreground=color)
        self.vox_status_msg.config(text=msg)

    def _populate_speakers(self, speakers_data: list[dict]):
        self.speaker_list = []
        for spk in speakers_data:
            for style in spk["styles"]:
                self.speaker_list.append({
                    "id": style["id"],
                    "label": f"{spk['name']}  ({style['name']})",
                })
        labels = [s["label"] for s in self.speaker_list]
        self.speaker_cb.config(values=labels, state="readonly")
        # デフォルト: ずんだもん(ノーマル) → なければ先頭
        default = 0
        for i, s in enumerate(self.speaker_list):
            if "ずんだもん" in s["label"] and "ノーマル" in s["label"]:
                default = i
                break
        self.speaker_cb.current(default)
        self._refresh_start_btn()

    def _set_buttons_idle(self):
        self.start_btn.config(state="normal")
        self.pause_btn.config(state="disabled", text="⏸  一時停止")
        self.stop_btn.config(state="disabled")

    # ─────────────────────────────────────────────────────────────────────────
    # VOICEVOX 起動スレッド
    # ─────────────────────────────────────────────────────────────────────────

    def _launch_voicevox(self):
        self.log_queue.put(("info", "VOICEVOX への接続を確認中..."))

        # すでに起動している場合
        spk = voicevox_get_speakers(self.voicevox_url)
        if spk is not None:
            self.log_queue.put(("ok", "VOICEVOX は既に起動しています"))
            self.status_queue.put(("vox_ok", spk))
            return

        # 実行ファイルを探す
        exe_str = self.voicevox_exe_var.get()
        exe = Path(exe_str) if exe_str else find_voicevox_executable()

        if exe is None or not exe.exists():
            self.log_queue.put(("error",
                "VOICEVOX の実行ファイルが見つかりませんでした。"))
            self.log_queue.put(("error",
                "「実行ファイルを指定...」で手動選択するか、VOICEVOX を起動してください。"))
            self.log_queue.put(("info",
                "VOICEVOX ダウンロード: https://voicevox.hiroshiba.jp/"))
            self.status_queue.put(("vox_error",))
            return

        self.log_queue.put(("info", f"VOICEVOX を起動中: {exe}"))
        try:
            self.voicevox_proc = subprocess.Popen(
                [str(exe)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception as e:
            self.log_queue.put(("error", f"起動エラー: {e}"))
            self.status_queue.put(("vox_error",))
            return

        # 起動を待機（最大 60 秒）
        self.log_queue.put(("info", "VOICEVOX の起動を待機中... (最大 60 秒)"))
        for _ in range(120):
            time.sleep(0.5)
            spk = voicevox_get_speakers(self.voicevox_url)
            if spk is not None:
                self.log_queue.put(
                    ("ok", f"VOICEVOX 接続成功！  話者数: {len(spk)} 名"))
                self.status_queue.put(("vox_ok", spk))
                return

        self.log_queue.put(("error", "VOICEVOX の起動がタイムアウトしました (60秒)"))
        self.status_queue.put(("vox_error",))

    # ─────────────────────────────────────────────────────────────────────────
    # 読み上げ制御
    # ─────────────────────────────────────────────────────────────────────────

    def _get_selected_speaker_id(self) -> int:
        idx = self.speaker_cb.current()
        if 0 <= idx < len(self.speaker_list):
            return self.speaker_list[idx]["id"]
        return 1

    def _start_reading(self):
        pdf_path = self.pdf_path_var.get()
        if not pdf_path:
            messagebox.showwarning("PDF 未選択", "PDF ファイルを選択してください。")
            return

        self.stop_event.clear()
        self.pause_event.clear()

        self.start_btn.config(state="disabled")
        self.pause_btn.config(state="normal", text="⏸  一時停止")
        self.stop_btn.config(state="normal")
        self.prog_bar["value"] = 0

        speaker_id = self._get_selected_speaker_id()
        speed = round(self.speed_var.get() * 10) / 10
        start_page = max(1, self.start_page_var.get())

        self._log("ok",
            f"読み上げ開始  話者ID:{speaker_id}  速度:{speed}×  "
            f"開始ページ:{start_page}")

        self.reader_thread = threading.Thread(
            target=self._reader_thread,
            args=(pdf_path, speaker_id, speed, start_page),
            daemon=True,
        )
        self.reader_thread.start()

    def _toggle_pause(self):
        if self.pause_event.is_set():
            self.pause_event.clear()
            self.pause_btn.config(text="⏸  一時停止")
            self._log("info", "再開しました")
        else:
            self.pause_event.set()
            self.pause_btn.config(text="▶  再開")
            self._log("info", "一時停止中...")

    def _stop_reading(self):
        self.stop_event.set()
        self.pause_event.clear()   # 一時停止中でも stop が通るよう解除
        pygame.mixer.stop()
        self._log("info", "停止しています...")

    # ─────────────────────────────────────────────────────────────────────────
    # 読み上げスレッド（プロデューサー/コンシューマー方式）
    #
    # 構造:
    #   [合成スレッド] チャンクを先読み合成して wav_queue へ積む
    #   [再生ループ ]  wav_queue から取り出して即再生
    #
    # これにより「再生中に次チャンクを合成」でき、チャンク間の無音を解消する。
    # ─────────────────────────────────────────────────────────────────────────

    def _reader_thread(
        self,
        pdf_path: str,
        speaker_id: int,
        speed: float,
        start_page: int,
    ):
        try:
            pages = extract_pages(pdf_path)
        except Exception as e:
            self.log_queue.put(("error", f"PDF 読み込み失敗: {e}"))
            self.status_queue.put(("stopped",))
            return

        total = len(pages)

        # ── 全チャンクをフラットなリストに展開 ────────────────────────────
        all_chunks: list[tuple] = []   # (page_num, total, ci, tc, chunk_text)
        for page_idx in range(start_page - 1, total):
            page_num = page_idx + 1
            text = pages[page_idx]
            if not text.strip():
                continue
            chunks = split_into_chunks(text)
            tc = len(chunks)
            for ci, chunk in enumerate(chunks, 1):
                all_chunks.append((page_num, total, ci, tc, chunk))

        if not all_chunks:
            self.log_queue.put(("info", "読み上げるテキストが見つかりませんでした"))
            self.status_queue.put(("done",))
            return

        # ── 先読みキュー (最大 2 チャンク分の WAV を保持) ────────────────
        # maxsize=2: 再生中に次・次々チャンクを合成しておける
        wav_queue: queue.Queue = queue.Queue(maxsize=2)
        synthesis_done = threading.Event()

        # ── 合成スレッド（プロデューサー）────────────────────────────────
        def synthesizer():
            for item in all_chunks:
                if self.stop_event.is_set():
                    break
                _, _, _, _, chunk = item
                wav = voicevox_synthesize(chunk, speaker_id, speed, self.voicevox_url)
                # キューが満杯のときは stop_event を見ながら待機
                while not self.stop_event.is_set():
                    try:
                        wav_queue.put((item, wav), timeout=0.2)
                        break
                    except queue.Full:
                        continue
            synthesis_done.set()

        syn_thread = threading.Thread(target=synthesizer, daemon=True)
        syn_thread.start()

        # ── 再生ループ（コンシューマー）──────────────────────────────────
        prev_page = -1
        while not self.stop_event.is_set():
            # キューからアイテム取得（空のときは synthesis_done を確認）
            try:
                entry = wav_queue.get(timeout=0.2)
            except queue.Empty:
                if synthesis_done.is_set():
                    break   # 全チャンク処理完了
                continue

            (page_num, total_p, ci, tc, chunk), wav = entry

            # ページ切り替わりをログに表示
            if page_num != prev_page:
                self.log_queue.put(("read", f"── ページ {page_num} / {total_p} ──"))
                prev_page = page_num

            # 一時停止中は待機（合成スレッドは先読みを継続）
            while self.pause_event.is_set() and not self.stop_event.is_set():
                time.sleep(0.08)

            if self.stop_event.is_set():
                break

            self.status_queue.put(("progress", page_num, total_p, ci, tc, chunk))
            self.log_queue.put(
                ("chunk", f"  [{ci}/{tc}] "
                          f"{chunk[:70]}{'…' if len(chunk) > 70 else ''}"))

            if wav:
                self._play_wav(wav)

        syn_thread.join(timeout=2)

        if self.stop_event.is_set():
            self.log_queue.put(("info", "読み上げを停止しました"))
            self.status_queue.put(("stopped",))
        else:
            self.log_queue.put(("ok", "読み上げ完了！"))
            self.status_queue.put(("done",))

    def _play_wav(self, wav_data: bytes):
        """一時停止・停止に対応した WAV 再生"""
        sound = pygame.mixer.Sound(io.BytesIO(wav_data))
        channel = sound.play()
        if channel is None:
            return

        while channel.get_busy():
            if self.stop_event.is_set():
                channel.stop()
                return
            if self.pause_event.is_set():
                channel.pause()
                while self.pause_event.is_set() and not self.stop_event.is_set():
                    time.sleep(0.05)
                if self.stop_event.is_set():
                    channel.stop()
                    return
                channel.unpause()
            time.sleep(0.05)

    # ─────────────────────────────────────────────────────────────────────────
    # 終了処理
    # ─────────────────────────────────────────────────────────────────────────

    def _on_close(self):
        self._stop_reading()
        if self.voicevox_proc is not None:
            try:
                self.voicevox_proc.terminate()
            except Exception:
                pass
        pygame.mixer.quit()
        self.destroy()


# ── エントリポイント ──────────────────────────────────────────────────────────

def main():
    initial_pdf = sys.argv[1] if len(sys.argv) > 1 else None
    app = App(initial_pdf=initial_pdf)
    app.mainloop()


if __name__ == "__main__":
    main()
