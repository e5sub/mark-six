"""ML 训练循环的纯函数数值核心（无 Flask / db 依赖）。

从 app.py 的 ML 训练器中抽取的可测试片段，供：
1) app.py 的训练/预测路径复用；
2) scripts/test_ml_optimizer.py 直接单测（无需 Flask）。

包含以下能力（与 app.py 现有数值逻辑保持一致，仅扩展了优化器）：
- softmax / sigmoid（数值稳定）
- per-dimension z-score 特征标准化（训练/预测同口径）
- special 头（softmax 交叉熵）梯度
- normal 头（sigmoid 多标签 BCE）梯度
- SGD 更新（不带动量，与现状一致）
- Adam 更新（偏差修正，L2 权重衰减与 SGD 对齐）
"""
import math


def softmax(values):
    if not values:
        return []
    max_value = max(values)
    exp_values = [math.exp(_clamp(value - max_value, -30.0, 30.0)) for value in values]
    total = sum(exp_values)
    if total <= 0:
        return [1.0 / len(values)] * len(values)
    return [value / total for value in exp_values]


def sigmoid(value):
    value = _clamp(float(value), -30.0, 30.0)
    if value >= 0:
        return 1.0 / (1.0 + math.exp(-value))
    exp_value = math.exp(value)
    return exp_value / (1.0 + exp_value)


def _clamp(value, low, high):
    return max(low, min(high, value))


# ---------------------------------------------------------------------------
# 特征标准化
# ---------------------------------------------------------------------------
def standardize_feature_map(feature_map, epsilon=1e-9):
    """对 49 个号码的特征行做 per-dimension z-score 标准化。

    feature_map: {"sno": [dim 特征值], ...}（48-49 行）。
    返回 (标准化后的同结构 map, stats)。
    stats = {"mean": [...], "std": [...]}，供预测阶段复用。
    方差过小的维度（std <= epsilon）保持 0.0，避免放大噪声特征。
    """
    rows = list(feature_map.values())
    if not rows:
        return {}, None
    dim = len(rows[0])
    n = len(rows)
    mean = [0.0] * dim
    for row in rows:
        for idx in range(dim):
            mean[idx] += row[idx]
    mean = [value / n for value in mean]
    var = [0.0] * dim
    for row in rows:
        for idx in range(dim):
            diff = row[idx] - mean[idx]
            var[idx] += diff * diff
    std = [math.sqrt(value / max(n - 1, 1)) for value in var]
    std_map = {}
    for key, row in feature_map.items():
        normalized_row = []
        for idx in range(dim):
            if std[idx] <= epsilon:
                normalized_row.append(0.0)
            else:
                normalized_row.append((row[idx] - mean[idx]) / std[idx])
        std_map[key] = normalized_row
    return std_map, {"mean": mean, "std": std}


# ---------------------------------------------------------------------------
# 梯度计算
# ---------------------------------------------------------------------------
def special_head_gradients(score_pairs, target_special):
    """special 头（49 类 softmax 交叉熵）梯度。

    score_pairs: [(key, features, score)]，features 为标准化后的特征。
    返回 (probabilities, grad_weights, grad_bias)：
      grad_weights = sum_i (label_i - prob_i) * features_i
      label_i = 1 if key == target_special else 0
    """
    probabilities = softmax([item[2] for item in score_pairs])
    grad_weights = None
    grad_bias = 0.0
    for row_idx, (key, features, _) in enumerate(score_pairs):
        label = 1.0 if key == target_special else 0.0
        error = label - probabilities[row_idx]
        grad_bias += error
        if grad_weights is None:
            grad_weights = [error * feature for feature in features]
        else:
            for feature_idx, feature_value in enumerate(features):
                grad_weights[feature_idx] += error * feature_value
    return probabilities, grad_weights or [], grad_bias


def normal_head_gradients(score_pairs, target_normals):
    """normal 头（每候选 sigmoid 二分类 BCE）梯度。

    target_normals: 本期的 6 个平码 set（不含 special）。正常期每个号码
    label = 1 if 号码 in target_normals else 0。
    返回 (sigmoid_scores, grad_weights, grad_bias)。
    """
    targets = set(target_normals or [])
    grad_weights = None
    grad_bias = 0.0
    sigmoid_scores = {}
    for row_idx, (key, features, value) in enumerate(score_pairs):
        probability = sigmoid(value)
        sigmoid_scores[key] = probability
        label = 1.0 if key in targets else 0.0
        error = label - probability
        grad_bias += error
        if grad_weights is None:
            grad_weights = [error * feature for feature in features]
        else:
            for feature_idx, feature_value in enumerate(features):
                grad_weights[feature_idx] += error * feature_value
    return sigmoid_scores, grad_weights or [], grad_bias


# ---------------------------------------------------------------------------
# Heuristic 系数（可在线学习的固定系数启发打分）
# ---------------------------------------------------------------------------
# 与 app.py 原有 _build_ml_heuristic_score_map 的魔法数完全一致（23 维特征）。
# features[12..14] 在公式里未使用，恒为 0.0；features[15/16] 是动量特征,
# 原公式取 max(0, x) 正部，这里用同样的处理。
HEURISTIC_DEFAULT_COEFFS = [
    0.22, 0.18, 0.10, 0.06, 0.12, 0.09,   # features[0..5]
    -0.08, -0.04,                          # features[6..7]（近距惩罚，负系数）
    0.07, 0.05, 0.06, 0.05,               # features[8..11]
    0.0, 0.0, 0.0,                         # features[12..14]（未使用）
    0.10, 0.08,                            # features[15..16]（动量，取正部）
    0.05, 0.06, -0.07, 0.05, 0.06, 0.03,  # features[17..22]
]
_HEURISTIC_RELU_INDICES = frozenset({15, 16})


def heuristic_effective_features(features):
    """把特征行扩展成与 HEURISTIC_DEFAULT_COEFFS 等长的有效行（15/16 取正部）。"""
    effective = [0.0] * len(HEURISTIC_DEFAULT_COEFFS)
    for idx, value in enumerate(features):
        if idx >= len(effective):
            break
        if idx in _HEURISTIC_RELU_INDICES:
            effective[idx] = max(0.0, float(value))
        else:
            effective[idx] = float(value)
    return effective


def heuristic_raw_score(features, coeffs=None):
    """启发打分（未归一化）；coeffs 缺省时用固定魔法系数。"""
    effective = heuristic_effective_features(features)
    if not coeffs:
        coeffs = HEURISTIC_DEFAULT_COEFFS
    total = 0.0
    for idx, value in enumerate(effective):
        if idx < len(coeffs):
            total += value * coeffs[idx]
    return total


def heuristic_regression_update(coeffs, samples, lr, clip=2.0):
    """轻量回归更新启发系数（每个评估期一次）。

    samples: [(raw_features, target_prob), ...]，target_prob ∈ [0,1]
    （normal 头 sigmoid 或 special 头 softmax 概率）。对每个样本做
    p = sigmoid(dot(coeffs, f))，误差 (p - t) 对系数求导后累积;
    每维梯度裁剪到 [-clip, clip]（原始特征量纲差异大，防单步越界），
    再走一步 lr * 梯度下降。系数整体裁剪到 [-1.0, 1.0] 防符号越界；
    输出形状仍由外层 _normalize_metric_map 归一成 [0,1]，量纲不变。
    """
    if not samples or not coeffs:
        return list(coeffs or [])
    dim = len(coeffs)
    gradient = [0.0] * dim
    for features, target in samples:
        effective = heuristic_effective_features(features)
        score = 0.0
        for idx in range(dim):
            score += effective[idx] * (coeffs[idx] if idx < len(coeffs) else 0.0)
        probability = sigmoid(score)
        error = probability - float(target)
        for idx in range(dim):
            gradient[idx] += error * effective[idx]
    count = len(samples)
    updated = []
    for idx in range(dim):
        mean_gradient = _clamp(gradient[idx] / count, -clip, clip)
        updated.append(_clamp(coeffs[idx] - lr * mean_gradient, -1.0, 1.0))
    return updated


# ---------------------------------------------------------------------------
# 温度校准（生产概率的温度缩放）
# ---------------------------------------------------------------------------
def tempered_softmax(values, temperature=1.0):
    """温度缩放的 softmax（对数缩放后再过 softmax）。温度≈1 时与 softmax 等价。"""
    temperature = float(temperature or 1.0)
    if temperature <= 1e-6 or not values:
        return softmax(values) if values else []
    return softmax([value / temperature for value in values])


def fit_temperature(samples, low=0.1, high=8.0, steps=48):
    """在网格上搜索使目标号码平均 log-loss 最小的温度 T（一维校准）。

    samples: [(keys: list, scores: list, target_key), ...]，keys 与 scores 等长，
    target_key 用 keys 中的元素标识目标行。返回一个正温度；
    样本无效或全平局时返回 1.0。
    """
    if not samples:
        return 1.0
    best_temperature = 1.0
    best_loss = float("inf")
    for step_idx in range(steps):
        temperature = low * ((high / low) ** (step_idx / max(steps - 1, 1)))
        total = 0.0
        for keys, scores, target in samples:
            probabilities = tempered_softmax(scores, temperature)
            if not probabilities:
                continue
            target_row = None
            for row_idx, key in enumerate(keys):
                if key == target:
                    target_row = row_idx
                    break
            if target_row is None:
                continue
            total += -math.log(max(probabilities[target_row], 1e-12))
        if total < best_loss:
            best_loss = total
            best_temperature = temperature
    return best_temperature


# ---------------------------------------------------------------------------
# 优化器步进
# ---------------------------------------------------------------------------
def sgd_update(weights, bias, grad_weights, grad_bias, step, l2):
    """普通 SGD（与现状 _update_ml_weights 一致：加 L2 衰减）。"""
    if grad_weights is None:
        return weights, bias
    for idx in range(len(grad_weights)):
        weights[idx] += step * (grad_weights[idx] - (l2 * weights[idx]))
    bias += step * grad_bias
    return weights, bias


def adam_init_state(dim):
    """返回 Adam 优化器状态（m/v/步数）。"""
    return {
        "m": [0.0] * dim,
        "v": [0.0] * dim,
        "bias_m": 0.0,
        "bias_v": 0.0,
        "t": 0,
    }


def adam_update(weights, bias, grad_weights, grad_bias, step, l2, state,
                beta1=0.9, beta2=0.999, epsilon=1e-8):
    """Adam 更新（偏差修正）；L2 作为对角正则（与 SGD 的 -l2*w 对齐）。"""
    if grad_weights is None:
        return weights, bias
    if state is None:
        state = adam_init_state(len(grad_weights))
    state["t"] += 1
    t = state["t"]
    for idx in range(len(grad_weights)):
        raw_grad = grad_weights[idx] - (l2 * weights[idx])
        state["m"][idx] = beta1 * state["m"][idx] + (1.0 - beta1) * raw_grad
        state["v"][idx] = beta2 * state["v"][idx] + (1.0 - beta2) * raw_grad * raw_grad
        m_hat = state["m"][idx] / (1.0 - beta1 ** t)
        v_hat = state["v"][idx] / (1.0 - beta2 ** t)
        weights[idx] += step * m_hat / (math.sqrt(v_hat) + epsilon)
    raw_bias = grad_bias
    state["bias_m"] = beta1 * state["bias_m"] + (1.0 - beta1) * raw_bias
    state["bias_v"] = beta2 * state["bias_v"] + (1.0 - beta2) * raw_bias * raw_bias
    bias_m_hat = state["bias_m"] / (1.0 - beta1 ** t)
    bias_v_hat = state["bias_v"] / (1.0 - beta2 ** t)
    bias += step * bias_m_hat / (math.sqrt(bias_v_hat) + epsilon)
    return weights, bias