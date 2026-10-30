# Third-Party Notices

live-subtitle itself is licensed under MIT. It downloads and uses the following models and depends on the following packages, each under its own license.

## Models

| Model | License | Attribution |
|---|---|---|
| quickmt/quickmt-zh-en, quickmt/quickmt-en-zh | CC-BY-4.0 | Translation models by **quickmt** (Mark Franey), https://huggingface.co/quickmt — used unmodified, converted outputs post-processed to Traditional Chinese with OpenCC. |
| OpenAI Whisper (tiny/base/small/large-v3-turbo) via Systran/faster-whisper-*, mobiuslabsgmbh (Dropbox) faster-whisper-large-v3-turbo, OpenVINO/whisper-*-ov | MIT / Apache-2.0 | © OpenAI; CTranslate2 conversions by SYSTRAN and Mobius Labs; OpenVINO conversions by Intel. |
| MediaTek-Research/Breeze-ASR-25 (via phate334/Breeze-ASR-25-ct2) | Apache-2.0 | © MediaTek Research. |
| Silero VAD (bundled in faster-whisper) | MIT | © Silero Team. |

## Python packages (main ones)

| Package | License |
|---|---|
| PySide6 / Qt | LGPL-3.0 (dynamically linked, unmodified; you may replace the Qt libraries in the virtual environment) |
| faster-whisper | MIT |
| CTranslate2 | MIT |
| OpenVINO, OpenVINO GenAI | Apache-2.0 |
| onnxruntime | MIT |
| sentencepiece | Apache-2.0 |
| sounddevice / PortAudio | MIT |
| numpy | BSD-3-Clause |
| opencc-python-reimplemented | Apache-2.0 |
| huggingface_hub | Apache-2.0 |
| platformdirs | MIT |
| tomli-w | MIT |
| nvidia-cublas-cu12, nvidia-cudnn-cu12 (CUDA extra only) | NVIDIA proprietary EULA (redistributable runtime) |

Full license texts are included in each package's distribution inside the virtual environment (`.venv/Lib/site-packages/*/LICENSE*`).
