"""Lightweight, zero-external-dependency NumPy ML & statistical evaluation suite.

Provides:
- StandardScaler
- LogisticRegressionIRLS (Newton-Raphson / IRLS with L2 penalty, exact SEs, z-stats, odds ratios)
- RidgeRegressor (closed-form L2 linear regression)
- FastHistogramGBDT (vectorized histogram-based Gradient Boosted Decision Trees for
  both binary classification and regression, supporting feature importances)
- Metrics: roc_auc_score, pr_auc_score, log_loss, brier_score, f1_score, mae, rmse, r2_score
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import numpy as np


class StandardScaler:
    """Zero-mean, unit-variance feature scaler."""

    def __init__(self) -> None:
        self.mean_: Optional[np.ndarray] = None
        self.scale_: Optional[np.ndarray] = None

    def fit(self, X: np.ndarray) -> "StandardScaler":
        self.mean_ = np.mean(X, axis=0)
        std = np.std(X, axis=0)
        self.scale_ = np.where(std > 1e-12, std, 1.0)
        return self

    def transform(self, X: np.ndarray) -> np.ndarray:
        return (X - self.mean_) / self.scale_

    def fit_transform(self, X: np.ndarray) -> np.ndarray:
        return self.fit(X).transform(X)


def _sigmoid(z: np.ndarray) -> np.ndarray:
    z = np.clip(z, -35.0, 35.0)
    return 1.0 / (1.0 + np.exp(-z))


class LogisticRegressionIRLS:
    """L2-regularized Logistic Regression solved via Iteratively Reweighted Least Squares (Newton-Raphson)."""

    def __init__(self, l2: float = 1.0, max_iter: int = 25, tol: float = 1e-6) -> None:
        self.l2 = l2
        self.max_iter = max_iter
        self.tol = tol
        self.coef_: Optional[np.ndarray] = None
        self.intercept_: float = 0.0
        self.se_: Optional[np.ndarray] = None
        self.z_scores_: Optional[np.ndarray] = None

    def fit(self, X: np.ndarray, y: np.ndarray) -> "LogisticRegressionIRLS":
        n, p = X.shape
        Xb = np.hstack([np.ones((n, 1)), X])
        beta = np.zeros(p + 1, dtype=np.float64)
        reg = np.eye(p + 1, dtype=np.float64) * self.l2
        reg[0, 0] = 0.0  # do not regularize intercept

        for _ in range(self.max_iter):
            probs = _sigmoid(Xb @ beta)
            w = np.clip(probs * (1.0 - probs), 1e-9, None)
            grad = Xb.T @ (probs - y) + reg @ beta
            H = (Xb.T * w) @ Xb + reg
            step = np.linalg.solve(H, grad)
            beta -= step
            if np.max(np.abs(step)) < self.tol:
                break

        probs = _sigmoid(Xb @ beta)
        w = np.clip(probs * (1.0 - probs), 1e-9, None)
        H = (Xb.T * w) @ Xb + reg
        cov = np.linalg.pinv(H)
        se = np.sqrt(np.maximum(np.diag(cov), 1e-18))

        self.intercept_ = float(beta[0])
        self.coef_ = beta[1:]
        self.se_ = se[1:]
        self.z_scores_ = self.coef_ / self.se_
        return self

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        return _sigmoid(self.intercept_ + X @ self.coef_)


class RidgeRegressor:
    """Closed-form L2-regularized Linear Regression."""

    def __init__(self, alpha: float = 1.0) -> None:
        self.alpha = alpha
        self.intercept_: float = 0.0
        self.coef_: Optional[np.ndarray] = None

    def fit(self, X: np.ndarray, y: np.ndarray) -> "RidgeRegressor":
        n, p = X.shape
        Xb = np.hstack([np.ones((n, 1)), X])
        reg = np.eye(p + 1, dtype=np.float64) * self.alpha
        reg[0, 0] = 0.0
        beta = np.linalg.solve(Xb.T @ Xb + reg, Xb.T @ y)
        self.intercept_ = float(beta[0])
        self.coef_ = beta[1:]
        return self

    def predict(self, X: np.ndarray) -> np.ndarray:
        return self.intercept_ + X @ self.coef_


@dataclass
class _TreeNode:
    is_leaf: bool
    value: float = 0.0
    feature: int = -1
    threshold: float = 0.0
    left: Optional["_TreeNode"] = None
    right: Optional["_TreeNode"] = None


class FastHistogramGBDT:
    """Fast vectorized Histogram Gradient Boosted Decision Trees (Binary Classification or Regression)."""

    def __init__(
        self,
        task: str = "classification",
        n_estimators: int = 60,
        learning_rate: float = 0.1,
        max_depth: int = 4,
        min_samples_leaf: int = 80,
        l2_leaf: float = 5.0,
        n_bins: int = 24,
    ) -> None:
        self.task = task
        self.n_estimators = n_estimators
        self.learning_rate = learning_rate
        self.max_depth = max_depth
        self.min_samples_leaf = min_samples_leaf
        self.l2_leaf = l2_leaf
        self.n_bins = n_bins
        self.init_score_: float = 0.0
        self.trees_: List[_TreeNode] = []
        self.feature_importances_: Optional[np.ndarray] = None
        self._bin_edges: List[np.ndarray] = []

    def _compute_bins(self, X: np.ndarray) -> Tuple[np.ndarray, List[np.ndarray]]:
        n, p = X.shape
        binned = np.zeros((n, p), dtype=np.int16)
        edges_list: List[np.ndarray] = []
        qs = np.linspace(0, 100, self.n_bins + 1)[1:-1]
        for j in range(p):
            col = X[:, j]
            edges = np.unique(np.percentile(col, qs))
            edges_list.append(edges)
            binned[:, j] = np.searchsorted(edges, col, side="right")
        return binned, edges_list

    def _build_node(
        self,
        X_bin: np.ndarray,
        g: np.ndarray,
        h: np.ndarray,
        idx: np.ndarray,
        depth: int,
        gains: np.ndarray,
    ) -> _TreeNode:
        G = float(np.sum(g[idx]))
        H = float(np.sum(h[idx]))
        leaf_val = -G / (H + self.l2_leaf)

        if depth >= self.max_depth or len(idx) < 2 * self.min_samples_leaf:
            return _TreeNode(is_leaf=True, value=leaf_val)

        parent_score = (G * G) / (H + self.l2_leaf)
        best_gain = 1e-6
        best_feat = -1
        best_bin = -1

        p = X_bin.shape[1]
        g_sub = g[idx]
        h_sub = h[idx]

        for j in range(p):
            edges = self._bin_edges[j]
            n_b = len(edges) + 1
            if n_b <= 1:
                continue
            b_sub = X_bin[idx, j]
            g_hist = np.bincount(b_sub, weights=g_sub, minlength=n_b)
            h_hist = np.bincount(b_sub, weights=h_sub, minlength=n_b)
            c_hist = np.bincount(b_sub, minlength=n_b)

            g_left = np.cumsum(g_hist)[:-1]
            h_left = np.cumsum(h_hist)[:-1]
            c_left = np.cumsum(c_hist)[:-1]
            c_right = len(idx) - c_left

            valid = (c_left >= self.min_samples_leaf) & (c_right >= self.min_samples_leaf)
            if not np.any(valid):
                continue

            g_right = G - g_left
            h_right = H - h_left
            scores = (g_left * g_left) / (h_left + self.l2_leaf) + (g_right * g_right) / (h_right + self.l2_leaf)
            gain_arr = np.where(valid, scores - parent_score, -1.0)
            k = int(np.argmax(gain_arr))
            if gain_arr[k] > best_gain:
                best_gain = float(gain_arr[k])
                best_feat = j
                best_bin = k

        if best_feat == -1:
            return _TreeNode(is_leaf=True, value=leaf_val)

        gains[best_feat] += best_gain
        mask_left = X_bin[idx, best_feat] <= best_bin
        left_node = self._build_node(X_bin, g, h, idx[mask_left], depth + 1, gains)
        right_node = self._build_node(X_bin, g, h, idx[~mask_left], depth + 1, gains)
        thresh = float(self._bin_edges[best_feat][best_bin])
        return _TreeNode(
            is_leaf=False,
            feature=best_feat,
            threshold=thresh,
            left=left_node,
            right=right_node,
        )

    def _predict_tree(self, node: _TreeNode, X: np.ndarray) -> np.ndarray:
        out = np.empty(X.shape[0], dtype=np.float64)
        stack = [(node, np.arange(X.shape[0]))]
        while stack:
            cur, idx = stack.pop()
            if cur.is_leaf:
                out[idx] = cur.value
            else:
                mask = X[idx, cur.feature] <= cur.threshold
                if np.any(mask):
                    stack.append((cur.left, idx[mask]))
                if np.any(~mask):
                    stack.append((cur.right, idx[~mask]))
        return out

    def fit(self, X: np.ndarray, y: np.ndarray) -> "FastHistogramGBDT":
        X = np.asarray(X, dtype=np.float64)
        y = np.asarray(y, dtype=np.float64)
        X_bin, self._bin_edges = self._compute_bins(X)
        n, p = X.shape
        gains = np.zeros(p, dtype=np.float64)
        all_idx = np.arange(n)

        if self.task == "classification":
            p_bar = np.clip(np.mean(y), 1e-6, 1.0 - 1e-6)
            self.init_score_ = float(np.log(p_bar / (1.0 - p_bar)))
        else:
            self.init_score_ = float(np.mean(y))

        raw = np.full(n, self.init_score_, dtype=np.float64)
        self.trees_ = []

        for _ in range(self.n_estimators):
            if self.task == "classification":
                probs = _sigmoid(raw)
                g = probs - y
                h = np.clip(probs * (1.0 - probs), 1e-6, None)
            else:
                g = raw - y
                h = np.ones(n, dtype=np.float64)

            tree = self._build_node(X_bin, g, h, all_idx, 0, gains)
            self.trees_.append(tree)
            raw += self.learning_rate * self._predict_tree(tree, X)

        total_gain = float(np.sum(gains))
        self.feature_importances_ = (gains / total_gain) if total_gain > 0 else np.zeros(p)
        return self

    def predict_raw(self, X: np.ndarray) -> np.ndarray:
        X = np.asarray(X, dtype=np.float64)
        raw = np.full(X.shape[0], self.init_score_, dtype=np.float64)
        for tree in self.trees_:
            raw += self.learning_rate * self._predict_tree(tree, X)
        return raw

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        return _sigmoid(self.predict_raw(X))

    def predict(self, X: np.ndarray) -> np.ndarray:
        return self.predict_raw(X)


# --- Evaluation Metrics ---

def roc_auc_score(y_true: np.ndarray, y_score: np.ndarray) -> float:
    y_true = np.asarray(y_true, dtype=np.float64)
    y_score = np.asarray(y_score, dtype=np.float64)
    order = np.argsort(-y_score)
    y_sorted = y_true[order]
    n_pos = np.sum(y_sorted == 1)
    n_neg = len(y_sorted) - n_pos
    if n_pos == 0 or n_neg == 0:
        return 0.5
    tps = np.cumsum(y_sorted == 1)
    fps = np.cumsum(y_sorted == 0)
    tpr = np.concatenate([[0.0], tps / n_pos])
    fpr = np.concatenate([[0.0], fps / n_neg])
    return float(np.trapezoid(tpr, fpr))


def pr_auc_score(y_true: np.ndarray, y_score: np.ndarray) -> float:
    y_true = np.asarray(y_true, dtype=np.float64)
    y_score = np.asarray(y_score, dtype=np.float64)
    order = np.argsort(-y_score)
    y_sorted = y_true[order]
    n_pos = np.sum(y_sorted == 1)
    if n_pos == 0:
        return 0.0
    tps = np.cumsum(y_sorted == 1)
    prec = tps / np.arange(1, len(y_sorted) + 1)
    rec = tps / n_pos
    return float(np.sum((rec - np.concatenate([[0.0], rec[:-1]])) * prec))


def log_loss(y_true: np.ndarray, y_prob: np.ndarray) -> float:
    p = np.clip(y_prob, 1e-9, 1.0 - 1e-9)
    return float(-np.mean(y_true * np.log(p) + (1.0 - y_true) * np.log(1.0 - p)))


def brier_score(y_true: np.ndarray, y_prob: np.ndarray) -> float:
    return float(np.mean((y_true - y_prob) ** 2))


def f1_score(y_true: np.ndarray, y_prob: np.ndarray, threshold: float = 0.5) -> float:
    pred = (y_prob >= threshold).astype(np.int32)
    tp = np.sum((pred == 1) & (y_true == 1))
    fp = np.sum((pred == 1) & (y_true == 0))
    fn = np.sum((pred == 0) & (y_true == 1))
    denom = 2 * tp + fp + fn
    return float(2 * tp / denom) if denom > 0 else 0.0


def regression_metrics(y_true: np.ndarray, y_pred: np.ndarray) -> Dict[str, float]:
    err = y_true - y_pred
    mae = float(np.mean(np.abs(err)))
    rmse = float(np.sqrt(np.mean(err ** 2)))
    ss_res = float(np.sum(err ** 2))
    ss_tot = float(np.sum((y_true - np.mean(y_true)) ** 2))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0
    return {"mae": round(mae, 3), "rmse": round(rmse, 3), "r2": round(r2, 4)}
