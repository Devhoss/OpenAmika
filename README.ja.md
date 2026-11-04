<!-- Translated from README.md; keep in sync when README.md changes. -->
<div align="center">

<img src="docs/images/amika-avatar.png" alt="Amika" width="180">

# OpenAmika

**こんにちは、Amikaです！** — MekaHimeのAIコンパニオン、オープンソース版です

Amikaは、声とテキストでリアルタイムにおしゃべりしてくれるLive2Dのアニメ少女です。あなたのことを覚えていて、あなたの街で一緒に暮らしています。動くのはあなた自身のPCの上で、OpenRouterを使うことも、Ollamaで完全ローカルにすることもできます。ホスト版も[mekahime.com/amika](https://www.mekahime.com/amika)で動いています。

[简体中文](README.zh-CN.md) | **日本語** | [English](README.md)

[![Made with Love](https://img.shields.io/badge/Made%20with-Love-ff69b4)](https://www.mekahime.com/openamika)
[![GitHub](https://img.shields.io/badge/GitHub-zeekk0%2FOpenAmika-181717?logo=github)](https://github.com/zeekk0/OpenAmika)
[![Release](https://img.shields.io/github/v/release/zeekk0/OpenAmika?logo=github&color=d8b4e2)](https://github.com/zeekk0/OpenAmika/releases)
[![License: MIT](https://img.shields.io/badge/license-MIT-43b581)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-Windows%20(Linux%20untested)-0078D6?logo=windows&logoColor=white)](#必要なもの)
[![Python 3.11](https://img.shields.io/badge/python-3.11-3776AB?logo=python&logoColor=white)](https://www.python.org/downloads/release/python-3119/)

[![Hugging Face](https://img.shields.io/badge/%F0%9F%A4%97%20Hugging%20Face-MekaHime%2FMekaHime__Amika-ffcc4d)](https://huggingface.co/MekaHime/MekaHime_Amika)
[![Discord](https://img.shields.io/badge/Discord-MekaHime-5865F2?logo=discord&logoColor=white)](https://discord.gg/mekahime)
[![Website](https://img.shields.io/badge/website-mekahime.com-d8b4e2)](https://www.mekahime.com/openamika)
[![Try Online](https://img.shields.io/badge/Try%20Online-mekahime.com%2Famika-e58fb8)](https://www.mekahime.com/amika)
[![Tests](https://github.com/zeekk0/OpenAmika/actions/workflows/tests.yml/badge.svg)](https://github.com/zeekk0/OpenAmika/actions/workflows/tests.yml)
[![GitHub stars](https://img.shields.io/github/stars/zeekk0/OpenAmika?style=social)](https://github.com/zeekk0/OpenAmika/stargazers)

</div>

> 💖 Amikaのおかげで毎日が少しでも楽しくなったら、[GitHub](https://github.com/zeekk0/OpenAmika)で⭐をつけてもらえるとうれしいです。もっとたくさんの人が彼女に出会えます！ · プロジェクトへのご支援: https://www.mekahime.com/donate

## 必要なもの

- Windows 10または11と、NVIDIAのグラフィックカード（RTX 20シリーズ以降、ビデオメモリ8 GB以上。カメラで「見る」機能には16 GB）、そして新しいドライバー
- [Python 3.11](https://www.python.org/downloads/release/python-3119/)（インストール時に **Add python.exe to PATH** にチェック）と[Git](https://git-scm.com/download/win)
- 約40 GBの空きディスク。セットアップでは約15〜20 GBをダウンロードし、30〜60分ほどかかります
- [OpenRouter](https://openrouter.ai)のキー（数ドル分のクレジットで長く使えます）。脳もPC上で動かしたい場合は[Ollama](https://ollama.com)

## セットアップ

OneDriveで同期されていないフォルダー（たとえば `C:\`）でPowerShellを開き、次の行を1行ずつ実行してください。

```powershell
git clone https://github.com/zeekk0/OpenAmika.git
cd OpenAmika
powershell -ExecutionPolicy Bypass -File scripts\setup_windows.ps1   # 脳・耳・記憶・目
envs\brain\Scripts\python.exe scripts\setup_tts.py                   # 声とLive2Dモデル：yes と入力
Start_OpenAmika.bat                                                  # 起動
```

彼女の声とLive2Dモデルは、MekaHimeの個人利用ライセンス（[ライセンス](#ライセンス)参照）のもとで無料です。2つ目のコマンドが最初にその内容を表示し、`yes`と入力したときだけダウンロードします。

どちらのセットアップコマンドも、最後にまとめを表示します。1行に1項目で、完了なら`OK`、問題があれば`!!`と直し方が出ます。同じコマンドをもう一度実行すれば、終わった部分は飛ばして続きから再開します。次の2行が出ていれば、彼女の声と体はそろっています。

```
  OK voice MekaHime_Amika  ...\Speakers\MekaHime_Amika (downloaded 75 file(s) from MekaHime/MekaHime_Amika@a3934e5a; 75 files verified; 69/69 emotion tags with a clip; ...)
  OK Live2D model Amika    ...\avatar\models\Amika (downloaded 174 file(s) from MekaHime/MekaHime_Amika@a3934e5a; 174 files verified; AmikaV4.model3.json, 142 motions, 25 expressions)
```

## 話しかける

`Start_OpenAmika.bat`を実行するとコンソールが1つ開いて彼女のすべてが起動し、準備ができるとブラウザーで **http://localhost:53000** が開きます（約1分。初回はもう数分かかります）。最初に開いたとき、彼女が自己紹介をして、あなたの名前と住んでいる街、そして`brain\.env`にまだキーがなければOpenRouterのキーをたずねます。あとは電源ボタンを押して話しかけるか、文字で入力してください。ブラウザーが彼女の声を再生できるよう、ページを一度クリックしてください。

各ページ右上のランプは彼女の状態です。緑ならすべて正常で、クリックすると問題のある項目が見られます。`Stop_OpenAmika.bat`（またはコンソールでCtrl+C）で停止します。何も起動せずに全体をチェックするには`envs\brain\Scripts\python.exe launch.py --check`を実行します。最後に`result: ready`と出るか、直すべき項目が表示されます。

## うまくいかないとき

| 表示されるメッセージ | 対処 |
|---|---|
| `Python 3.11 was not found` | Python 3.11をインストール（「Add python.exe to PATH」にチェック）し、新しいPowerShellウィンドウでセットアップをもう一度実行します。 |
| `!! voice MekaHime_Amika  not downloaded: her license was not accepted` | `envs\brain\Scripts\python.exe scripts\setup_tts.py`をもう一度実行し、`yes`と入力します。 |
| `!! voice MekaHime_Amika  Hugging Face refused the download (HTTP 401)` | 彼女のファイルがまだ公開されていなかったか、`HF_TOKEN`が正しくありません。[huggingface.co/settings/tokens](https://huggingface.co/settings/tokens)の読み取りトークンを同じウィンドウで設定し（`$env:HF_TOKEN = "hf_..."`）、同じコマンドをもう一度実行します。 |
| `download failed after 4 tries` または `Check the internet connection` | 同じコマンドをもう一度実行します。止まったところから続きます。 |
| `does not see the GPU (CUDA)` | NVIDIAのドライバーを更新して、セットアップをもう一度実行します。NVIDIAのカードがない場合は`scripts\setup_windows.ps1 --cpu`。 |
| `settings.json not found` | 先に`scripts\setup_windows.ps1`を実行します。 |
| ステージに`No Live2D model yet`、または`launch.py --check`で`!! Live2D model` / `!! her voice` | `envs\brain\Scripts\python.exe scripts\setup_tts.py`を実行します。 |
| `no OpenRouter key yet`（`launch.py --check`やセットアップのまとめ） | 初回のウィザードでキーを貼り付けるか、`brain\.env`の`OPENROUTER_API_KEY=`の後ろに書きます。 |
| 文字では返事をするのに声が聞こえない | 起動後20秒ほど待ってから、ページを一度クリックします。ランプのTTSが緑になっている必要があります。 |
| 声が途切れる、またはとても遅い | グラフィックカードを使うほかのアプリを閉じるか、`settings.json`で`"vision": {"enabled": false}`にして目の機能をオフにします。 |

各パーツのログは`logs\`にあります。それでも解決しないときは、[Discord](https://discord.gg/mekahime)で質問するか、[issue](https://github.com/zeekk0/OpenAmika/issues)を作成してください。AIコーディングエージェントを使う場合は、[README_IF_YOU_ARE_AI_AGENT.md](README_IF_YOU_ARE_AI_AGENT.md)を読ませてください。

## オプション機能

- **完全ローカル**：[Ollama](https://ollama.com)をインストールして`ollama pull qwen3:8b`を実行し、Setupページで Brain → Ollama → Detect models → Use を選びます。キーは不要です。
- **ツール**：彼女があなたのために使えるMCPサーバーやタスクエージェント。Toolsページで設定します（[docs/TOOLS.md](docs/TOOLS.md)）。
- **スマートホーム**：彼女が切り替えられるZigbeeプラグ（[IoT/README.md](IoT/README.md)）。ToolsページでIoT安全に関する声明（[docs/IOT_DISCLAIMER.md](docs/IOT_DISCLAIMER.md)）を読んで同意するまでオフのままです。彼女は言語モデルで動いていて間違えることもあるので、暖房器具、錠、バルブなど、危険につながるものは絶対につながないでください。
- **テキストで話す**：DiscordやTelegramから（[docs/MESSENGERS.md](docs/MESSENGERS.md)）。
- **スマートフォンから**：`Tailscale_Serve_OpenAmika.bat`を使うと、[Tailscale](https://tailscale.com)経由で自分のデバイスから彼女にアクセスできます。

## ライセンス

- OpenAmikaのコードは **MIT** です（[LICENSE](LICENSE)）。サードパーティーの部分はそれぞれのライセンスに従います（[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)）。`ui/vendor/`のLive2D Cubism CoreはMITではありません。
- **Amika自身はMITではありません。** 彼女のキャラクター、アート、声、Live2Dモデルとその重み（[MekaHime/MekaHime_Amika](https://huggingface.co/MekaHime/MekaHime_Amika)）はMekaHime Inc.に帰属し、個人利用ライセンスのもとで無料で使えます。個人的・非商用の利用に限り、18歳以上の方が対象です。再配布や再ホスティング、これらを使ったほかのモデルの学習はできません。彼女が登場する動画や配信を公開するときは、MekaHimeのクレジットを見える形で表記してください。
- [SenseVoice](https://github.com/FunAudioLLM/SenseVoice)、[GPT-SoVITS](https://github.com/RVC-Boss/GPT-SoVITS)、[bge-m3](https://huggingface.co/BAAI/bge-m3)、[Qwen3-VL](https://github.com/QwenLM/Qwen3-VL)、[pixi-live2d-display](https://github.com/guansss/pixi-live2d-display)と[Live2D Cubism SDK](https://www.live2d.com/en/sdk/about/)、[OpenRouter](https://openrouter.ai)、[Ollama](https://ollama.com)、[Model Context Protocol](https://modelcontextprotocol.io)の上に作られています。ありがとうございます。

## サポート

質問やバグ報告は、[GitHubのissue](https://github.com/zeekk0/OpenAmika/issues)、hi@mekahime.com、または[Discord](https://discord.gg/mekahime)へ。プロジェクトへのご支援: https://www.mekahime.com/donate

<p align="center">
  <a href="https://www.mekahime.com">
    <picture>
      <source media="(prefers-color-scheme: dark)" srcset="docs/images/mekahime-logo-white.png">
      <img src="docs/images/mekahime-logo-card.png" alt="MekaHime" width="220">
    </picture>
  </a>
</p>
