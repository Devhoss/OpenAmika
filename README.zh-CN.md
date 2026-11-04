<!-- Translated from README.md; keep in sync when README.md changes. -->
<div align="center">

<img src="docs/images/amika-avatar.png" alt="Amika" width="180">

# OpenAmika

**你好！我是 Amika**——MekaHime 的 AI 伴侣，现已开源

Amika 是一个 Live2D 二次元少女：她能用语音和文字与你实时聊天，会记住你，还和你一起住在你所在的城市。她运行在你自己的电脑上，可以接入 OpenRouter，也可以借助 Ollama 完全本地运行。你也可以在 [mekahime.com/amika](https://www.mekahime.com/amika) 体验在线托管版。

**简体中文** | [日本語](README.ja.md) | [English](README.md)

[![Made with Love](https://img.shields.io/badge/Made%20with-Love-ff69b4)](https://www.mekahime.com/openamika)
[![GitHub](https://img.shields.io/badge/GitHub-zeekk0%2FOpenAmika-181717?logo=github)](https://github.com/zeekk0/OpenAmika)
[![Release](https://img.shields.io/github/v/release/zeekk0/OpenAmika?logo=github&color=d8b4e2)](https://github.com/zeekk0/OpenAmika/releases)
[![License: MIT](https://img.shields.io/badge/license-MIT-43b581)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-Windows%20(Linux%20untested)-0078D6?logo=windows&logoColor=white)](#准备工作)
[![Python 3.11](https://img.shields.io/badge/python-3.11-3776AB?logo=python&logoColor=white)](https://www.python.org/downloads/release/python-3119/)

[![Hugging Face](https://img.shields.io/badge/%F0%9F%A4%97%20Hugging%20Face-MekaHime%2FMekaHime__Amika-ffcc4d)](https://huggingface.co/MekaHime/MekaHime_Amika)
[![Discord](https://img.shields.io/badge/Discord-MekaHime-5865F2?logo=discord&logoColor=white)](https://discord.gg/mekahime)
[![Website](https://img.shields.io/badge/website-mekahime.com-d8b4e2)](https://www.mekahime.com/openamika)
[![Try Online](https://img.shields.io/badge/Try%20Online-mekahime.com%2Famika-e58fb8)](https://www.mekahime.com/amika)
[![Tests](https://github.com/zeekk0/OpenAmika/actions/workflows/tests.yml/badge.svg)](https://github.com/zeekk0/OpenAmika/actions/workflows/tests.yml)
[![GitHub stars](https://img.shields.io/github/stars/zeekk0/OpenAmika?style=social)](https://github.com/zeekk0/OpenAmika/stargazers)

</div>

> 💖 如果 Amika 让你的一天变得更美好，就在 [GitHub](https://github.com/zeekk0/OpenAmika) 上给我们点个 ⭐ 吧——这能帮更多人找到她！· 支持本项目：https://www.mekahime.com/donate

## 准备工作

- Windows 10 或 11，以及一块 NVIDIA 显卡（RTX 20 系列或更新，显存 8 GB 以上；她的摄像头“眼睛”需要 16 GB），驱动保持较新
- [Python 3.11](https://www.python.org/downloads/release/python-3119/)（安装时勾选 **Add python.exe to PATH**）和 [Git](https://git-scm.com/download/win)
- 约 45 GB 可用磁盘空间。安装过程会下载约 15–20 GB，耗时 30–60 分钟
- 一个 [OpenRouter](https://openrouter.ai) 密钥（充值几美元就能用很久）；如果想让她的大脑也在本机运行，可以用 [Ollama](https://ollama.com)

## 安装

打开 PowerShell（普通窗口，不要以管理员身份运行），逐行运行下面的命令。它们会把她安装在 `C:\OpenAmika`——一个不被 OneDrive 同步的短路径文件夹：

```powershell
cd C:\
git clone https://github.com/zeekk0/OpenAmika.git
cd OpenAmika
powershell -ExecutionPolicy Bypass -File scripts\setup_windows.ps1   # 她的大脑、耳朵、记忆和眼睛
envs\brain\Scripts\python.exe scripts\setup_tts.py                   # 她的声音和 Live2D 模型：输入 yes
.\Start_OpenAmika.bat                                                # 启动她
```

她的声音和 Live2D 模型依照 MekaHime 的个人使用许可证免费提供（见[许可证](#许可证)）；`setup_tts.py` 这一行会先显示许可证内容，只有在你输入 `yes` 之后才会下载她的文件。

两条安装命令最后都会输出一份汇总：每个部分一行，完成显示 `OK`，有问题显示 `!!` 并附上解决方法，`--` 只是提示信息（例如还没有 OpenRouter 密钥：第一次打开页面时会请你填写）。再次运行同一条命令会从中断处继续，已完成的部分会跳过。`setup_tts.py` 的汇总里出现 `OK voice MekaHime_Amika` 和 `OK Live2D model Amika`，就说明她的声音和身体都已就位。

## 和她聊天

双击 OpenAmika 文件夹里的 `Start_OpenAmika.bat`（在 PowerShell 里运行 `.\Start_OpenAmika.bat`）。它会打开一个控制台窗口并启动她的全部组件；准备就绪后，浏览器会打开 **http://localhost:53000**（大约一分钟，第一次会多几分钟）。第一次打开时，她会先做自我介绍，问你的名字和所在城市；如果 `brain\.env` 里还没有密钥，也会请你填入 OpenRouter 密钥。之后按下电源按钮就可以和她说话，或者打字。请先在页面上点击一次，浏览器才会允许她发出声音。

每个页面右上角的指示灯显示她的状态：绿色表示一切正常，点击它可以查看哪里有问题。双击 `Stop_OpenAmika.bat`（或在她的控制台里按 Ctrl+C）停止她。如果想在不启动任何东西的情况下检查一遍，运行 `envs\brain\Scripts\python.exe launch.py --check`：最后会显示 `result: ready`，或者列出需要修复的地方。

更新她：先停止她，在 OpenAmika 文件夹里运行 `git stash`、`git pull`、`git stash pop`（会保留你的名字、所在城市和你的修改），再把两条安装命令各运行一次（只会获取有变化的部分）。

## 遇到问题时

| 你看到的信息 | 解决方法 |
|---|---|
| `Python 3.11 was not found` | 安装 Python 3.11（勾选 “Add python.exe to PATH”），打开一个新的 PowerShell 窗口，重新运行安装。 |
| `!! voice MekaHime_Amika  not downloaded: her license was not accepted` | 重新运行 `envs\brain\Scripts\python.exe scripts\setup_tts.py`，并输入 `yes`。 |
| `!! voice MekaHime_Amika  Hugging Face refused the download (HTTP 401)` | 她的文件还没有公开，或者你的 `HF_TOKEN` 不对。在同一个窗口里设置 [huggingface.co/settings/tokens](https://huggingface.co/settings/tokens) 上的只读令牌（`$env:HF_TOKEN = "hf_..."`），再运行一次同一条命令。 |
| `download failed after 4 tries` 或 `Check the internet connection` | 再运行一次同一条命令，会从中断处继续。如果你的网络连不上 huggingface.co，先把 `$env:HF_ENDPOINT` 设为一个 Hugging Face 镜像；每个文件照样会被校验。 |
| `does not see the GPU (CUDA)` | 更新 NVIDIA 驱动后重新运行安装。没有 NVIDIA 显卡时：`powershell -ExecutionPolicy Bypass -File scripts\setup_windows.ps1 --cpu`。 |
| `The filename or extension is too long` | 文件夹路径对 Windows 来说太长了：把 OpenAmika 移到 `C:\OpenAmika` 这样的短路径文件夹，再在那里运行安装。 |
| `settings.json not found` | 先运行 `powershell -ExecutionPolicy Bypass -File scripts\setup_windows.ps1`。 |
| 舞台上显示 `No Live2D model yet`，或 `launch.py --check` 里出现 `!! Live2D model` / `!! her voice` | 运行 `envs\brain\Scripts\python.exe scripts\setup_tts.py`。 |
| `no OpenRouter key yet`（`launch.py --check` 或安装汇总中） | 在首次运行的向导里粘贴密钥，或写在 `brain\.env` 的 `OPENROUTER_API_KEY=` 后面。 |
| `!! TTS (voice)  port 8002 answers but has no voice MekaHime_Amika` | 另一个语音服务器占用了她的端口（OpenSakura 的语音服务器也用 8002）。关掉它，或者给她换个端口：把 `settings.json` 里 `tts_url` 和 `services.tts`（`--port` 与 `health`）中的 `8002` 改成别的端口。 |
| 她用文字回复，但听不到声音 | 启动后等大约 20 秒，再在页面上点击一次。指示灯里的 TTS 必须是绿色。 |
| 她的声音断断续续或非常慢 | 关闭其他占用显卡的程序，或者在 `settings.json` 里设置 `"vision": {"enabled": false}` 关掉她的眼睛。 |

每个组件的日志都在 `logs\` 里。其他问题可以到 [Discord](https://discord.gg/mekahime) 提问，或提交 [issue](https://github.com/zeekk0/OpenAmika/issues)。如果你在用 AI 编程助手，请让它阅读 [README_IF_YOU_ARE_AI_AGENT.md](README_IF_YOU_ARE_AI_AGENT.md)。

## 可选功能

- **完全本地运行**：安装 [Ollama](https://ollama.com)，运行 `ollama pull qwen3:4b`，然后在 Setup 页面选择 Brain → Ollama → Detect models → Use this model。不需要密钥。`qwen3:4b` 在 12–16 GB 显存的显卡上可以和她一起运行（16 GB 时请关掉她的眼睛：在 `settings.json` 里设置 `"vision": {"enabled": false}`）；更大的模型需要第二块 GPU，或在另一台电脑上运行的 Ollama（Setup 页面可以填写它的地址）。
- **工具**：她可以替你使用的 MCP 服务器和任务代理，在 Tools 页面设置（[docs/TOOLS.md](docs/TOOLS.md)）。
- **智能家居**：她可以开关的 Zigbee 插座（[IoT/README.md](IoT/README.md)）。在你于 Tools 页面阅读并接受 IoT 安全声明（[docs/IOT_DISCLAIMER.md](docs/IOT_DISCLAIMER.md)）之前，它始终关闭。她运行在大语言模型之上，可能出错：绝对不要连接取暖器、门锁、阀门或任何可能造成危险的设备。
- **用文字找她**：通过 Discord 或 Telegram（[docs/MESSENGERS.md](docs/MESSENGERS.md)）。
- **用手机访问**：`Tailscale_Serve_OpenAmika.bat` 可以让你通过 [Tailscale](https://tailscale.com) 在自己的设备上访问她。

## 许可证

- OpenAmika 的代码采用 **MIT** 许可证（[LICENSE](LICENSE)）。第三方部分遵循各自的许可证（[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)）；`ui/vendor/` 中的 Live2D Cubism Core 不是 MIT。
- **Amika 本身不是 MIT。** 她的角色、美术、声音、Live2D 模型及其权重（[MekaHime/MekaHime_Amika](https://huggingface.co/MekaHime/MekaHime_Amika)）归 MekaHime Inc. 所有，依照个人使用许可证免费提供：仅限个人、非商业用途，须年满 18 岁，不得再分发或重新托管，不得用于训练其他模型；发布有她出镜的视频或直播时，须清晰可见地注明 MekaHime。
- 本项目构建于 [SenseVoice](https://github.com/FunAudioLLM/SenseVoice)、[GPT-SoVITS](https://github.com/RVC-Boss/GPT-SoVITS)、[bge-m3](https://huggingface.co/BAAI/bge-m3)、[Qwen3-VL](https://github.com/QwenLM/Qwen3-VL)、[pixi-live2d-display](https://github.com/guansss/pixi-live2d-display) 与 [Live2D Cubism SDK](https://www.live2d.com/en/sdk/about/)、[OpenRouter](https://openrouter.ai)、[Ollama](https://ollama.com) 和 [Model Context Protocol](https://modelcontextprotocol.io) 之上。谢谢你们。

## 支持

问题和 bug 反馈：[GitHub issues](https://github.com/zeekk0/OpenAmika/issues)、hi@mekahime.com，或 [Discord](https://discord.gg/mekahime)。支持本项目：https://www.mekahime.com/donate

<p align="center">
  <a href="https://www.mekahime.com">
    <picture>
      <source media="(prefers-color-scheme: dark)" srcset="docs/images/mekahime-logo-white.png">
      <img src="docs/images/mekahime-logo-card.png" alt="MekaHime" width="220">
    </picture>
  </a>
</p>
