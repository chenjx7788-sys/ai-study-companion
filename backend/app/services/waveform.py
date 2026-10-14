"""音频包络提取：从音轨解码 → 按帧算 RMS 包络 → 归一化 LEVELS 数组。

供「解压型」皮肤（`radio` / `night`）的**音频响应波形**驱动：
舞台页按 `LEVELS[floor(ms·fps/1000)]` 取当前帧的响度，让波形跟着人声起伏
（解压视觉方案 V2.1 §4.1 / §4.2）。

⚠️ 波形是**预计算的离散包络、由 ms 索引**（`ms → 状态` 纯函数），不是 AudioContext
   实时分析 —— 这是渲染链路 `seekTo(ms)` + 逐帧截图能「同果」的前提（§6.4）。
⚠️ 用**真实人声**音轨才有起伏；静音/匀速正弦音轨的 RMS 几乎恒定 → 波形不动。
   闸门用 `envelope_activity()` 拦这种「静音轨配波形」的死画面（§8.2）。
"""
from __future__ import annotations

from pathlib import Path

# 波形不需要高保真：16kHz 单声道足够，且比 48k 解码快 3 倍。
SAMPLE_RATE = 16000
# 包络帧率与视频帧率对齐（舞台页按帧索引）。
DEFAULT_FPS = 30


def audio_levels(audio_path, fps: int = DEFAULT_FPS) -> list[float]:
    """解码音轨 → 按帧（`fps`）算 RMS → 峰值归一化到 0..1，返回逐帧响度数组。

    失败（文件不存在 / 解不出）抛 `RuntimeError`，由调用方决定降级（不静默给空数组 —
    空数组会让波形恒平，正是要避免的「死画面」）。
    """
    import av
    import numpy as np

    p = Path(audio_path)
    if not p.exists():
        raise RuntimeError(f"音轨文件不存在：{p}")

    container = av.open(str(p))
    try:
        stream = container.streams.audio[0]
        resampler = av.audio.resampler.AudioResampler(
            format="s16", layout="mono", rate=SAMPLE_RATE)
        chunks: list = []
        for frame in container.decode(stream):
            out = resampler.resample(frame)
            if out is None:
                continue
            if not isinstance(out, (list, tuple)):
                out = [out]
            for rf in out:
                if rf is not None:
                    chunks.append(rf.to_ndarray().reshape(-1))
        # 冲刷重采样器尾部缓冲
        tail = resampler.resample(None)
        if tail:
            for rf in (tail if isinstance(tail, (list, tuple)) else [tail]):
                if rf is not None:
                    chunks.append(rf.to_ndarray().reshape(-1))
    finally:
        try:
            container.close()
        except BaseException:
            pass

    if not chunks:
        raise RuntimeError(f"音轨解码为空：{p}")

    pcm = np.concatenate(chunks).astype("float64") / 32768.0
    per = max(1, int(round(SAMPLE_RATE / fps)))
    n_frames = len(pcm) // per
    if n_frames <= 0:
        raise RuntimeError(f"音轨太短（不足一帧）：{p}")

    lv = np.sqrt((pcm[:n_frames * per].reshape(n_frames, per) ** 2).mean(axis=1))
    peak = float(lv.max())
    if peak <= 1e-6:
        # 静音轨：归一化会把噪声放大成满幅乱跳 → 直接给全 0，由闸门拦（envelope_activity）
        return [0.0] * n_frames
    return [round(float(x / peak), 4) for x in lv]


def envelope_activity(levels: list[float]) -> dict:
    """波形包络的「起伏度」—— 闸门判「静音轨配波形不动」用（**渲染前可判**，§8.2）。

    返回 `{voiced_ratio, p90, active}`：
      · voiced_ratio：有声帧（>0.02）占比；
      · p90：包络 90 分位（静音轨归一化后为全 0 → p90=0）；
      · active：是否有足够起伏（有声帧占比 ≥20% 且 p90 ≥ 0.15）。
    """
    if not levels:
        return {"voiced_ratio": 0.0, "p90": 0.0, "active": False}
    import numpy as np
    a = np.asarray(levels, dtype=float)
    voiced = float((a > 0.02).mean())
    p90 = float(np.percentile(a, 90))
    return {"voiced_ratio": round(voiced, 3), "p90": round(p90, 3),
            "active": bool(voiced >= 0.2 and p90 >= 0.15)}
