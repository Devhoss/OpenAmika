# Third-party notices: OpenAmika voice server

`main.py` in this folder is MekaHime's own code (MIT, see `LICENSE`), derived from the OpenSakura voice server
(<https://github.com/zeekk0/OpenSakura>, MIT, also MekaHime's). It runs on upstream GPT-SoVITS and
on public models that `scripts/setup_tts.py` downloads onto your machine. None of those files are in
this repository; each keeps its own license, listed here.

## GPT-SoVITS (code)

Downloaded unmodified from <https://github.com/RVC-Boss/GPT-SoVITS> at commit
`b4570b876f5c086819ba84e3215472733c6a7b70` into `GPT-SoVITS/`. MIT License:

```
MIT License

Copyright (c) 2024 RVC-Boss

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

Parts of the upstream tree carry their own notices (for example the ERes2NetV2 speaker-verification
code under `GPT_SoVITS/eres2net/`, Apache-2.0, and AP-BWE under `tools/AP_BWE_main/`, MIT); see the
headers and LICENSE files inside the checkout.

## Models and data downloaded by setup_tts.py

| file(s) | from | license |
|---|---|---|
| `chinese-hubert-base/` (HuBERT, speech features) | Hugging Face `lj1995/GPT-SoVITS` (repo license: MIT); original TencentGameMate/chinese-hubert-base | MIT |
| `chinese-roberta-wwm-ext-large/` (BERT, Chinese text features) | `lj1995/GPT-SoVITS`; original HFL hfl/chinese-roberta-wwm-ext-large | Apache-2.0 (original) |
| `sv/pretrained_eres2netv2w24s4ep4.ckpt` (speaker embedding for v2Pro models) | `lj1995/GPT-SoVITS`; original 3D-Speaker iic/speech_eres2netv2w24s4ep4_sv_zh-cn_16k-common | Apache-2.0 (original) |
| `G2PWModel/` (Chinese polyphone model) | Hugging Face `XXXXRT/GPT-SoVITS-Pretrained` (repo license: MIT); original GitYCC/g2pW | Apache-2.0 (original) |
| `fast_langdetect/lid.176.bin` (language ID) | fastText, <https://fasttext.cc/docs/en/language-identification.html> | CC BY-SA 3.0 |
| `nltk_data/taggers/averaged_perceptron_tagger*` | nltk/nltk_data | MIT |
| `nltk_data/corpora/cmudict` | nltk/nltk_data, CMU Pronouncing Dictionary 0.6 | unrestricted use, acknowledgement requested (Carnegie Mellon University) |
| `ffmpeg/` (Windows: gyan.dev 8.0.1 essentials build; Linux: BtbN static build) | <https://www.gyan.dev/ffmpeg/builds/>, <https://github.com/BtbN/FFmpeg-Builds> | GPL-3.0 builds of FFmpeg; run as a separate program, never linked |

Python packages installed into `.venv/` (torch, transformers, onnxruntime, pyopenjtalk-plus with its
bundled Open JTalk dictionary, OpenCC, jieba, ...) are under their own licenses; `pip show <name>` lists them.

## Amika's voice and Live2D model

`Speakers/MekaHime_Amika/` (her voice) and `avatar/models/Amika/` (her Live2D model, outside this folder) come
from the Hugging Face repository `MekaHime/MekaHime_Amika`. They are MekaHime Inc. assets licensed for personal,
hobbyist use only (no distillation, training, extraction, resale or commercial use); the full terms are in the
`LICENSE` file that is downloaded with the voice. They are not covered by the MIT license of this folder. The
Live2D model runs on Live2D Cubism, whose software is licensed by Live2D Inc.
