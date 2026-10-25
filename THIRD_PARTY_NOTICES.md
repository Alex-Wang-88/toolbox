# Third-party voice engines

This project supports the following third-party local voice clone engine:

- **CosyVoice 3** (`tts_poc/CosyVoice`): 阿里通义实验室开源的零样本语音克隆模型，
  模型权重位于 `tts_poc/models/CosyVoice3-0.5B/`，运行在独立 venv
  `tts_poc/venv_cosyvoice` 中，通过子进程 JSON-RPC（`src/tts_workers/cosyvoice3_worker.py`）
  被主流程调用。CosyVoice 3 使用其自身项目/模型许可证，商用或大规模使用前请审阅其原始协议。

- **Seed-VC** (`tts_poc/Seed-VC`, optional): zero-shot voice conversion and singing
  voice conversion engine used by the optional AI 唱歌 page. It is kept as an
  external, ignored checkout and is invoked through its own Python environment;
  it is not copied into the tracked application source. Seed-VC is distributed
  under GPL-3.0. Review its repository and model terms before redistributing a
  package with the engine enabled.

- **Demucs** (optional Python dependency): two-stem vocal/accompaniment separation
  used by AI 唱歌的原曲模式. It is invoked in the configured external Python
  environment and is not bundled into the main Flask process. Demucs is distributed
  under the MIT License; review the repository and model terms before redistribution.

- **cryptography** (optional main-process dependency): AES-ECB decryption used by
  the local NCM container converter. It is an independent third-party package;
  review its Apache-2.0/BSD-3-Clause license notices before redistribution.

Reference voices must only be uploaded when the user has the right to use that
voice. The application requires an explicit authorization confirmation before
upload and stores the confirmation with the private voice record.
