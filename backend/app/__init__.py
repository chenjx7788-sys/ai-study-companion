"""AI 伴学助手 · 后端应用包

⚠️ 这里必须在**任何 numpy / BLAS 相关模块被导入之前**设置线程环境变量。

原因（实测，2026-09-11）：Windows 上 numpy 自带的 OpenBLAS 会按 CPU 核心数预分配
线程缓冲区，核心数多 + 内存偏紧时直接失败，终端只留下：

    OpenBLAS error: Memory allocation still failed after 10 retries, giving up.

表现为 numpy 完全不可用，且**症状伪装成「内存不足」**——曾据此误判为
Whisper 模型加载失败（其实同一根因）。限制为单线程后一切正常。

代价可忽略：本项目的本地计算只有 FFT 滤波与向量运算，性能瓶颈在 LLM / TTS
的网络调用，而不在本地 BLAS。反之若在这里失败，整个应用直接不可用。
"""
import os

for _var in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS",
             "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ.setdefault(_var, "1")
