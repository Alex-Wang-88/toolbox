# Third-party voice engines

This project supports the following third-party local voice clone engine:

- **CosyVoice 3** (`tts_poc/CosyVoice`): 阿里通义实验室开源的零样本语音克隆模型，
  模型权重位于 `tts_poc/models/CosyVoice3-0.5B/`，运行在独立 venv
  `tts_poc/venv_cosyvoice` 中，通过子进程 JSON-RPC（`src/tts_workers/cosyvoice3_worker.py`）
  被主流程调用。CosyVoice 3 使用其自身项目/模型许可证，商用或大规模使用前请审阅其原始协议。

Reference voices must only be uploaded when the user has the right to use that
voice. The application requires an explicit authorization confirmation before
upload and stores the confirmation with the private voice record.
