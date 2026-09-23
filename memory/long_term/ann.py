# -*- coding: utf-8 -*-
"""ANN 索引（hnswlib / HNSW 图索引）：速度优先检索 + 两阶段重排保准确率。

- 速度：索引常驻内存，图搜索对数级复杂度，单查毫秒级；增量 add 支持运行中持续写入；
- 准确率：query 超取 overfetch 个候选（默认 50）→ 调用方用缓存原始向量做精确 cosine 重排
  → recall@k 对齐暴力精确检索（参数 M / ef_construction / ef_search 可调）；
- 万人隔离：knn_query(filter=label 谓词) 单索引服务全体用户（label=chunk_id）；
  更大规模时可按用户分片为多个小索引（扩展路径，接口不变）。
"""
from __future__ import annotations

import math
import threading
from pathlib import Path

# hnswlib 为可选依赖：PyPI 上没有 Windows 预编译包，缺 MSVC 时只能源码编译。
# 缺失时本模块仍可导入（AnnIndex 一实例化才报错），
# 长期记忆的 facts / 范式条目 / 知识库索引不受影响，仅"向量图索引"降级。
try:
    import hnswlib
except ImportError:  # pragma: no cover - 无 C++ 编译环境的 Windows
    hnswlib = None

HNSWLIB_AVAILABLE: bool = hnswlib is not None

_HNSWLIB_HINT = (
    "未安装 hnswlib（PyPI 无 Windows 预编译包，需 Visual C++ Build Tools 源码编译）。"
    "向量图索引不可用；长期记忆的 facts / 范式条目 / 知识库索引不受影响，"
    "文档语义召回将退回暴力精确检索（brute_force_recall）。"
)


def _require_hnswlib() -> None:
    """实例化 ANN 索引前的可用性检查：缺失时给出可操作的报错而非 ModuleNotFoundError。"""
    if not HNSWLIB_AVAILABLE:
        raise RuntimeError(_HNSWLIB_HINT)


def l2_normalize(vec: list[float]) -> list[float]:
    """L2 归一化（零向量防除零）；归一化后内积即 cosine 相似度。"""
    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / norm for x in vec]


def cosine(a: list[float], b: list[float]) -> float:
    """精确 cosine 相似度（重排阶段用；不要求输入已归一化）。"""
    num = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(y * y for y in b)) or 1.0
    return num / (na * nb)


class AnnIndex:
    """hnswlib 封装：label 即 chunk_id，向量全部 L2 归一化后入索引（cosine 语义一致）。"""

    def __init__(
        self,
        dim: int,
        space: str = "cosine",
        M: int = 32,
        ef_construction: int = 200,
        ef_search: int = 64,
        overfetch: int = 50,
        initial_capacity: int = 2048,
    ):
        _require_hnswlib()
        self.dim = dim
        self.space = space
        self.M = M
        self.ef_construction = ef_construction
        self.ef_search = ef_search
        self.overfetch = overfetch
        self._lock = threading.Lock()
        self._index = hnswlib.Index(space=space, dim=dim)
        self._index.init_index(max_elements=initial_capacity, ef_construction=ef_construction, M=M)
        self._index.set_ef(ef_search)

    # ---------- 写 ----------
    def add(self, labels: list[int], vectors: list[list[float]]) -> None:
        """批量写入（label=chunk_id，向量自动归一化）；容量不足自动翻倍扩容。"""
        if not labels:
            return
        vecs = [l2_normalize(v) for v in vectors]
        with self._lock:
            try:
                cap = self._index.get_max_elements()
            except AttributeError:  # 旧版兜底
                cap = max(self._index.get_current_count(), 1024)
            if self._index.get_current_count() + len(vecs) > cap:
                self._index.resize_index(max(cap * 2, self._index.get_current_count() + len(vecs)))
            self._index.add_items(vecs, [int(x) for x in labels])

    def mark_deleted(self, label: int) -> None:
        """软删除一个 label（HNSW 不物理删除；label 不存在时静默忽略）。"""
        with self._lock:
            try:
                self._index.mark_deleted(int(label))
            except Exception:
                pass  # label 不存在时忽略

    # ---------- 读（ANN 粗排）----------
    def query(self, vector: list[float], k: int, label_filter=None) -> list[tuple[int, float]]:
        """返回 [(label, 相似度)] 候选（已按 ANN 距离排序）；k 仅为上限，实际取 overfetch。"""
        q = l2_normalize(vector)
        with self._lock:
            n = self._index.get_current_count()
            if n == 0:
                return []
            kk = min(self.overfetch, n)
            while kk >= 1:  # 带过滤时命中数可能少于 kk，逐步降 k 重试
                try:
                    self._index.set_ef(max(self.ef_search, kk + 16))
                    labels, dists = self._index.knn_query([q], k=kk, filter=label_filter)
                    break
                except Exception:
                    kk //= 2
            else:
                return []
            return [
                (int(lb), 1.0 - float(d))
                for lb, d in zip(labels[0], dists[0])
                if int(lb) >= 0
            ]

    def __len__(self) -> int:
        """索引中的元素总数（含软删除项）。"""
        return self._index.get_current_count()

    # ---------- 持久化 ----------
    def save(self, path: str | Path) -> None:
        """索引序列化到 .hnsw 文件（自动建父目录）。"""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            self._index.save_index(str(path))

    @classmethod
    def load(cls, path: str | Path, dim: int, ef_search: int = 64, overfetch: int = 50) -> "AnnIndex":
        """从 .hnsw 文件恢复索引（绕过 __init__，HNSW 建图参数已在文件内）。"""
        _require_hnswlib()
        obj = cls.__new__(cls)
        obj.dim = dim
        obj.space = "cosine"
        obj.M = 32
        obj.ef_construction = 200
        obj.ef_search = ef_search
        obj.overfetch = overfetch
        obj._lock = threading.Lock()
        obj._index = hnswlib.Index(space="cosine", dim=dim)
        obj._index.load_index(str(path))
        obj._index.set_ef(ef_search)
        return obj
