#!/usr/bin/env python3
"""
PDF VOICEVOX Reader
PDFファイルを読み込み、VOICEVOXで上から順に読み上げるアプリ

使い方:
  python pdf_voicevox_reader.py document.pdf
  python pdf_voicevox_reader.py document.pdf --speaker 3 --speed 1.3
  python pdf_voicevox_reader.py document.pdf --page 5
  python pdf_voicevox_reader.py --list-speakers
"""

import sys
import io
import re
import time
import argparse
import requests
import fitz  # PyMuPDF
import pygame

# VOICEVOX デフォルトURL
DEFAULT_VOICEVOX_URL = "http://localhost:50021"


# ---------------------------------------------------------------------------
# PDF テキスト抽出
# ---------------------------------------------------------------------------

def extract_pages(pdf_path: str) -> list[str]:
    """PDFの各ページからテキストを抽出してリストで返す"""
    doc = fitz.open(pdf_path)
    pages = []
    for page in doc:
        text = page.get_text()
        pages.append(text)
    doc.close()
    return pages


def clean_text(text: str) -> str:
    """PDF抽出テキストの余分な空白・改行を整理する"""
    # 連続する改行を1つにまとめる
    text = re.sub(r'\n{3,}', '\n\n', text)
    # 行末のスペースを除去
    text = re.sub(r' +\n', '\n', text)
    # ハイフンで改行されている単語をつなぐ（英文PDF向け）
    text = re.sub(r'-\n([a-z])', r'\1', text)
    return text.strip()


def split_into_chunks(text: str, max_chars: int = 120) -> list[str]:
    """
    テキストを VOICEVOX が扱いやすい長さのチャンクに分割する。
    句読点（。！？ . ! ?）を優先的な区切りとして使う。
    """
    text = clean_text(text)
    if not text:
        return []

    # 句読点 + 空白 / 改行 で文を分割
    raw_sentences = re.split(r'(?<=[。！？\.\!\?])\s+|(?<=\n)', text)

    chunks: list[str] = []
    current = ""

    for sentence in raw_sentences:
        sentence = sentence.strip()
        if not sentence:
            continue

        if len(current) + len(sentence) + 1 <= max_chars:
            current = (current + " " + sentence).strip() if current else sentence
        else:
            if current:
                chunks.append(current)
            # 1文が長すぎる場合は強制分割
            if len(sentence) > max_chars:
                for i in range(0, len(sentence), max_chars):
                    chunks.append(sentence[i : i + max_chars])
                current = ""
            else:
                current = sentence

    if current:
        chunks.append(current)

    return chunks


# ---------------------------------------------------------------------------
# VOICEVOX API
# ---------------------------------------------------------------------------

def check_voicevox(base_url: str) -> list[dict] | None:
    """VOICEVOXに接続して話者一覧を取得する。失敗時は None を返す"""
    try:
        resp = requests.get(f"{base_url}/speakers", timeout=5)
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.ConnectionError:
        return None
    except Exception:
        return None


def synthesize(text: str, speaker: int, speed: float, base_url: str) -> bytes | None:
    """
    テキストを音声合成して WAV バイト列を返す。
    失敗時は None を返す。
    """
    try:
        # Step 1: audio_query でパラメータ取得
        resp = requests.post(
            f"{base_url}/audio_query",
            params={"text": text, "speaker": speaker},
            timeout=15,
        )
        resp.raise_for_status()
        query = resp.json()

        # 速度・音量調整
        query["speedScale"] = speed

        # Step 2: synthesis で音声バイト列取得
        resp = requests.post(
            f"{base_url}/synthesis",
            params={"speaker": speaker},
            json=query,
            timeout=30,
        )
        resp.raise_for_status()
        return resp.content

    except requests.exceptions.Timeout:
        print("  [警告] タイムアウト。スキップします。")
        return None
    except Exception as e:
        print(f"  [合成エラー] {e}")
        return None


# ---------------------------------------------------------------------------
# 音声再生
# ---------------------------------------------------------------------------

def play_wav(wav_data: bytes) -> None:
    """WAV バイト列を pygame で再生し、完了まで待機する"""
    sound = pygame.mixer.Sound(io.BytesIO(wav_data))
    sound.play()
    while pygame.mixer.get_busy():
        pygame.time.wait(50)


# ---------------------------------------------------------------------------
# ユーティリティ
# ---------------------------------------------------------------------------

def print_speakers(speakers: list[dict]) -> None:
    """話者一覧を見やすく表示する"""
    print("\n利用可能な話者一覧")
    print("=" * 45)
    for spk in speakers:
        print(f"  {spk['name']}")
        for style in spk["styles"]:
            print(f"    ID: {style['id']:3d}  スタイル: {style['name']}")
    print()


def truncate(text: str, width: int = 70) -> str:
    """表示用に長いテキストを省略する"""
    return text[:width] + "..." if len(text) > width else text


# ---------------------------------------------------------------------------
# メイン
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="PDFを上から順にVOICEVOXで読み上げるアプリ",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
例:
  # 基本的な使い方
  python pdf_voicevox_reader.py earnings_call.pdf

  # 話者・速度を指定
  python pdf_voicevox_reader.py earnings_call.pdf --speaker 3 --speed 1.4

  # 5ページ目から読み上げ
  python pdf_voicevox_reader.py earnings_call.pdf --page 5

  # 利用可能な話者を確認
  python pdf_voicevox_reader.py --list-speakers

注意:
  事前にVOICEVOXを起動しておく必要があります。
  VOICEVOX: https://voicevox.hiroshiba.jp/
        """,
    )
    parser.add_argument("pdf", nargs="?", help="読み上げる PDF ファイルのパス")
    parser.add_argument(
        "--speaker", type=int, default=3,
        help="話者 ID (デフォルト: 3 ずんだもん)"
    )
    parser.add_argument(
        "--speed", type=float, default=1.0,
        help="読み上げ速度 0.5〜2.0 (デフォルト: 1.0)"
    )
    parser.add_argument(
        "--page", type=int, default=1,
        help="読み上げを開始するページ番号 (デフォルト: 1)"
    )
    parser.add_argument(
        "--chunk-size", type=int, default=120,
        help="一度に送信する最大文字数 (デフォルト: 120)"
    )
    parser.add_argument(
        "--list-speakers", action="store_true",
        help="話者一覧を表示して終了"
    )
    parser.add_argument(
        "--voicevox-url", default=DEFAULT_VOICEVOX_URL,
        help=f"VOICEVOX の URL (デフォルト: {DEFAULT_VOICEVOX_URL})"
    )
    args = parser.parse_args()

    base_url = args.voicevox_url.rstrip("/")

    # ── VOICEVOX 接続確認 ──────────────────────────────────
    print(f"VOICEVOXに接続中... ({base_url})")
    speakers = check_voicevox(base_url)
    if speakers is None:
        print(
            "\nエラー: VOICEVOXに接続できませんでした。\n"
            "VOICEVOXを起動してから再度実行してください。\n"
            "ダウンロード: https://voicevox.hiroshiba.jp/"
        )
        sys.exit(1)
    print(f"接続成功！  話者数: {len(speakers)} 名")

    # 話者一覧表示モード
    if args.list_speakers:
        print_speakers(speakers)
        sys.exit(0)

    if not args.pdf:
        parser.print_help()
        sys.exit(1)

    # ── PDF 読み込み ────────────────────────────────────────
    print(f"\nPDF を読み込み中: {args.pdf}")
    try:
        pages = extract_pages(args.pdf)
    except FileNotFoundError:
        print(f"エラー: ファイルが見つかりません: {args.pdf}")
        sys.exit(1)
    except Exception as e:
        print(f"エラー: PDF を開けませんでした: {e}")
        sys.exit(1)

    total_pages = len(pages)
    start_page = max(1, min(args.page, total_pages))

    print(f"総ページ数: {total_pages}")
    print(f"話者 ID  : {args.speaker}")
    print(f"読み上げ速度: {args.speed}x")
    print(f"開始ページ: {start_page}")
    print("\nCtrl+C で停止できます。\n")
    print("=" * 55)

    # ── pygame 初期化 ───────────────────────────────────────
    pygame.mixer.init(frequency=24000, size=-16, channels=1, buffer=512)

    # ── 読み上げループ ──────────────────────────────────────
    try:
        for page_idx in range(start_page - 1, total_pages):
            page_num = page_idx + 1
            page_text = pages[page_idx]

            if not page_text.strip():
                print(f"\n── ページ {page_num}/{total_pages} (空白ページ) ──")
                continue

            print(f"\n── ページ {page_num}/{total_pages} ──")

            chunks = split_into_chunks(page_text, args.chunk_size)
            total_chunks = len(chunks)

            for chunk_idx, chunk in enumerate(chunks, 1):
                if not chunk.strip():
                    continue

                print(f"  [{chunk_idx}/{total_chunks}] {truncate(chunk)}")

                wav = synthesize(chunk, args.speaker, args.speed, base_url)
                if wav:
                    play_wav(wav)

    except KeyboardInterrupt:
        print("\n\n読み上げを停止しました。")
    finally:
        pygame.mixer.quit()

    print("\n読み上げ完了！")


if __name__ == "__main__":
    main()
