"""Document Parser 抽象基类 + 异常体系 + 共享 IO helper（Phase 3.3）。

设计原则：
    - Parser 不知道 DB / LLM / Chunk（单一职责）
    - Parser 接口统一：`parse(file_path) -> str`
    - 异常**显式**分类，绝不静默吞掉
    - IO helper 仅负责"读 UTF-8 文本"，不含业务语义

未来扩展：
    - PDFParser / DocxParser / ExcelParser
    - 只需新建模块并在 factory._REGISTRY 注册；不修改 base 接口
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

__all__ = [
    "DocumentParseError",
    "DocumentNotFoundError",
    "UnsupportedDocumentTypeError",
    "DocumentParser",
    "read_text_file",
]


# ============================================================
# 异常体系
# ============================================================

class DocumentParseError(Exception):
    """通用文档解析错误基类。

    所有 Parser 抛出的异常均继承自本类；
    调用方可以 `except DocumentParseError` 统一捕获。
    """


class DocumentNotFoundError(DocumentParseError, FileNotFoundError):
    """文档不存在（FileNotFoundError 多继承，便于上层用 OSError 捕获）。

    - 路径不存在（文件/目录均无）
    - 调用方可以同时当作 FileNotFoundError 处理
    """


class UnsupportedDocumentTypeError(DocumentParseError):
    """不支持的文档类型（扩展名未注册到 factory）。"""


# ============================================================
# Parser 抽象基类
# ============================================================

class DocumentParser(ABC):
    """所有 Document Parser 的抽象基类。

    责任边界：
        输入：文件路径（.md / .txt / 未来 .pdf / .docx / ...）
        输出：纯文本字符串（保留 Markdown 标题等结构）
        **不**触碰：DB / LLM / Chunk / Embedding / Vector Search
    """

    @property
    @abstractmethod
    def file_type(self) -> str:
        """Parser 支持的文件扩展名（不含点号、小写，如 `'md'` / `'txt'`）。"""

    def supports(self, file_type: str) -> bool:
        """是否支持指定扩展名（用于 factory 之外的场景）。"""
        return file_type.strip().lower().lstrip(".") == self.file_type

    @abstractmethod
    def parse(self, file_path: str | Path) -> str:
        """读取文件并解析为纯文本。

        Args:
            file_path: 文件路径（str 或 pathlib.Path）。

        Returns:
            纯文本字符串。空文件返回 `""`。

        Raises:
            DocumentNotFoundError: 文件不存在。
            DocumentParseError: 其他解析错误（编码 / IO / 权限等）。
        """


# ============================================================
# 共享 IO helper（不依赖具体 Parser）
# ============================================================

def read_text_file(
    file_path: str | Path,
    *,
    encoding: str = "utf-8",
) -> str:
    """读取 UTF-8 文本文件（仅 IO，不含任何业务语义）。

    适用于所有"读 UTF-8 文本"的 Parser（当前：TXT / Markdown）。

    Args:
        file_path: 文件路径。
        encoding: 文本编码（默认 UTF-8）。

    Returns:
        文件内容字符串。

    Raises:
        DocumentNotFoundError: 路径不存在或不是文件。
        DocumentParseError: 解码失败（编码不是 UTF-8）或 IO 错误。
    """
    path = Path(file_path)
    if not path.exists():
        raise DocumentNotFoundError(f"文件不存在: {path}")
    if not path.is_file():
        raise DocumentParseError(f"路径不是文件: {path}")

    try:
        return path.read_text(encoding=encoding)
    except UnicodeDecodeError as exc:
        raise DocumentParseError(
            f"文件编码错误（仅支持 {encoding}）: {path}"
        ) from exc
    except OSError as exc:
        raise DocumentParseError(f"读取文件失败: {path}: {exc}") from exc