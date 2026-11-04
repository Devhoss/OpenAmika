# SenseVoiceSmall (speech recognition)

The speech layer uses [SenseVoiceSmall](https://github.com/FunAudioLLM/SenseVoice) (FunAudioLLM / FunASR) for
fully local speech-to-text (ASR + language ID + emotion + audio-event tags). Its files are **not** in this
repository. `scripts\setup_windows.ps1` (`scripts/setup_core.py`) fetches them; by hand (any Python 3 runs it):

```powershell
envs\brain\Scripts\python.exe scripts\download_sensevoice.py
```

The script downloads the six files below from ModelScope (Hugging Face as a fallback), checks each against the
SHA-256 of the official `iic/SenseVoiceSmall` release, and keeps what is already correct, so a re-run after a
network error only fetches what is missing. `--verify` checks without downloading.

```
speech/models/
├── model.pt                                (~936 MB, the weights)
├── config.yaml
├── configuration.json
├── am.mvn
├── chn_jpn_yue_eng_ko_spectok.bpe.model
└── tokens.json
```

By hand instead: download those files from https://www.modelscope.cn/models/iic/SenseVoiceSmall into this
folder, or with the `modelscope` package that speech/requirements.txt installs:

```powershell
envs\speech\Scripts\python.exe -c "from modelscope import snapshot_download; snapshot_download('iic/SenseVoiceSmall', local_dir='speech/models')"
```

GPU strongly recommended (`speech_device: "cuda:0"` in settings.json); `"cpu"` works but adds latency.
License: the model files are distributed by FunAudioLLM under the FunASR Model Open Source License Agreement;
the SenseVoice code in `speech/model.py` and `speech/utils/` is MIT (see `speech/LICENSE-SenseVoice`).
