"""重复噪声记录的保守高值筛选；不修改原始时序或删除频率点。"""
import math
import numpy as np


def select_noise_records(scores, policy="mad_high"):
    """仅剔除组内显著高值；小样本或无正基线时不自动筛除。"""
    scores = np.asarray(scores, dtype=float)
    if scores.ndim != 1 or not len(scores) or not np.isfinite(scores).all() or np.any(scores < 0):
        raise ValueError("噪声记录评分必须为非空、有限且非负的一维数组")
    if policy not in ("mad_high", "none"):
        raise ValueError("未知噪声异常记录处理策略")
    median = float(np.median(scores))
    mad = float(np.median(np.abs(scores - median)))
    threshold = max(3 * median, median + 6 * 1.4826 * mad)
    eligible = len(scores) >= 5 and median > 0
    candidates = (scores > threshold) if eligible else np.zeros(len(scores), dtype=bool)
    keep = ~candidates if policy == "mad_high" else np.ones(len(scores), dtype=bool)
    return keep, dict(policy=policy, median_record_asd_V_per_sqrtHz=median,
        mad_record_asd_V_per_sqrtHz=mad, threshold_asd_V_per_sqrtHz=threshold,
        minimum_records=5, candidate_indices=np.flatnonzero(candidates).tolist(),
        rule="score > max(3 * median, median + 6 * 1.4826 * MAD); high side only; at least 5 records")


def inspect_noise_spectra(frequency, matrix, low, high, policy="mad_high"):
    """按共同频带的中位 ASD 评分，返回掩码、评分及剔除前后平均 PSD。"""
    frequency, matrix = np.asarray(frequency, dtype=float), np.asarray(matrix, dtype=float)
    if (frequency.ndim != 1 or frequency.size < 2 or not np.isfinite(frequency).all()
            or np.any(frequency < 0) or np.any(np.diff(frequency) <= 0)
            or matrix.ndim != 2 or matrix.shape != (len(matrix), frequency.size)
            or not len(matrix) or not np.isfinite(matrix).all() or np.any(matrix < 0)):
        raise ValueError("噪声频率轴或逐记录 PSD 矩阵无效")
    if not (math.isfinite(low) and math.isfinite(high) and 0 < low < high):
        raise ValueError("噪声评分频带必须满足 0 < 下限 < 上限")
    band = (frequency >= low) & (frequency <= high)
    if frequency[-1] < high or band.sum() < 2:
        raise ValueError("噪声质量检查所需的公共频带未被实际 PSD 完整覆盖")
    scores = np.median(np.sqrt(matrix[:, band]), axis=1)
    keep, quality = select_noise_records(scores, policy)
    return keep, scores, np.mean(matrix, axis=0), np.mean(matrix[keep], axis=0), quality
