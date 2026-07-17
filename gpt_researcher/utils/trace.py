"""
轻量级异步调用链跟踪工具。

用法：在关键 async 函数上加 @trace 装饰器即可。
自动记录：文件路径、行号、函数名、入参摘要、耗时。
按调用深度缩进打印树状调用链。
"""

import asyncio
import functools
import inspect
import time
from contextvars import ContextVar, Token
from typing import Any, Dict, Optional

# --- 全局开关 ---
TRACE_ENABLED: bool = True

# --- 每个 asyncio task 独立的调用栈 ---
_trace_stack: ContextVar[list] = ContextVar("_trace_stack", default=[])
_trace_indent_cache: ContextVar[int] = ContextVar("_trace_indent", default=0)


def _summarize_value(v, max_item_len=60) -> str:
    """把单个值转成可读摘要字符串。"""
    if v is None:
        return "None"
    if isinstance(v, bool):
        return str(v)
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, str):
        if len(v) <= max_item_len:
            # 换行替换为空格，避免日志断行
            return repr(v.replace("\n", " "))
        return repr(v[:max_item_len].replace("\n", " ") + "...")
    if isinstance(v, (list, tuple, set)):
        inner = ", ".join(_summarize_value(x, 30) for x in v[:3])
        if len(v) > 3:
            inner += f", ...({len(v)} items)"
        return f"[{inner}]" if isinstance(v, list) else f"({inner})"
    if isinstance(v, dict):
        # 显示部分 key
        keys = list(v.keys())[:5]
        key_str = ", ".join(_summarize_value(k, 20) for k in keys)
        if len(v) > 5:
            key_str += f", ...({len(v)} keys)"
        return f"{{{key_str}}}"
    # 尝试 repr，太长就回退到类型名
    try:
        s = repr(v)
        if len(s) <= 50:
            return s
    except Exception:
        pass
    return type(v).__name__


def _summarize_args(args, kwargs, max_len=120) -> str:
    """精简函数入参，避免打印整个对象。"""
    parts = []
    # 跳过 self/cls (第一个参数且类型名匹配函数所属类时跳过)
    for v in args:
        s = _summarize_value(v)
        parts.append(s)
    for k, v in kwargs.items():
        s = f"{k}={_summarize_value(v)}"
        parts.append(s)
    joined = ", ".join(parts)
    if len(joined) > max_len:
        joined = joined[:max_len - 3] + "..."
    return joined


def _trace_push(span: dict) -> Token:
    stack = _trace_stack.get().copy()
    stack.append(span)
    return _trace_stack.set(stack)


def _trace_pop(token: Token) -> Optional[dict]:
    stack = _trace_stack.get().copy()
    span = stack.pop() if stack else None
    _trace_stack.reset(token)
    return span


def _get_indent() -> int:
    return len(_trace_stack.get())


def _get_func_doc(func) -> str:
    """提取函数 docstring 的第一行作为功能描述。"""
    doc = inspect.getdoc(func)
    if doc:
        first_line = doc.split("\n")[0].strip()
        if first_line:
            return first_line
    return ""


def _print_span(span: dict, indent: int):
    prefix = "│  " * indent
    node = span["node"]
    file_line = f"{span['file']}:{span['line']}"
    args_str = span.get("args", "")
    doc = span.get("doc", "")
    elapsed = span.get("elapsed_ms", 0)

    # 功能描述（docstring 第一行）
    doc_str = ""
    if doc:
        doc_str = f" \033[33m# {doc}\033[0m"

    # 只打印入口 + 耗时，不打印 [END]
    elapsed_str = ""
    if elapsed > 0:
        elapsed_str = f" \033[2m({elapsed:.0f}ms)\033[0m"
    if span.get("error"):
        elapsed_str += f" \033[31m✗ {span['error']}\033[0m"

    print(f"{prefix}\033[36m{file_line}\033[0m \033[1m{node}\033[0m({args_str}){doc_str}{elapsed_str}")
    import sys
    sys.stdout.flush()


def _safe_function_name(func) -> str:
    """获取可读的函数全名：ClassName.method_name 或 module.func_name"""
    qualname = getattr(func, "__qualname__", func.__name__)
    module = getattr(func, "__module__", "")
    # 如果是方法（qualname 里含 .），尝试把 class 名带上
    if "." in qualname:
        return qualname
    if module:
        return f"{module}.{qualname}"
    return qualname


def _function_file_line(func) -> tuple:
    """返回 (相对文件路径, 行号)"""
    try:
        file = inspect.getfile(func)
        # 转成相对于 workspace 的路径
        file = file.replace("/home/azhengya/code_space/gpt-researcher/", "")
        sourcelines, lineno = inspect.getsourcelines(func)
        return file, lineno
    except (TypeError, OSError):
        return getattr(func, "__module__", "?"), 0


def trace(func):
    """
    异步函数调用链跟踪装饰器。

    在函数入口打印一行带缩进的调用信息（文件路径:行号 函数名(参数)）。
    contextvars 保证并发 asyncio task 间互不干扰。
    """

    @functools.wraps(func)
    async def async_wrapper(*args, **kwargs):
        if not TRACE_ENABLED:
            return await func(*args, **kwargs)

        file, line = _function_file_line(func)
        name = _safe_function_name(func)
        args_summary = _summarize_args(args, kwargs)
        func_doc = _get_func_doc(func)

        span = {
            "node": name,
            "file": file,
            "line": line,
            "args": args_summary,
            "doc": func_doc,
            "start": time.time(),
            "elapsed_ms": 0,
            "error": None,
        }

        indent = _get_indent()
        _print_span(span, indent)

        token = _trace_push(span)
        try:
            result = await func(*args, **kwargs)
            return result
        except Exception as e:
            span["error"] = f"{type(e).__name__}: {e}"
            raise
        finally:
            _trace_pop(token)

    @functools.wraps(func)
    def sync_wrapper(*args, **kwargs):
        if not TRACE_ENABLED:
            return func(*args, **kwargs)

        file, line = _function_file_line(func)
        name = _safe_function_name(func)
        args_summary = _summarize_args(args, kwargs)
        func_doc = _get_func_doc(func)

        span = {
            "node": name,
            "file": file,
            "line": line,
            "args": args_summary,
            "doc": func_doc,
            "start": time.time(),
            "elapsed_ms": 0,
            "error": None,
        }

        indent = _get_indent()
        _print_span(span, indent)

        token = _trace_push(span)
        try:
            result = func(*args, **kwargs)
            return result
        except Exception as e:
            span["error"] = f"{type(e).__name__}: {e}"
            raise
        finally:
            _trace_pop(token)

    if asyncio.iscoroutinefunction(func):
        async_wrapper._trace_wrapped = True
        return async_wrapper
    sync_wrapper._trace_wrapped = True
    return sync_wrapper


def auto_trace_module(module, *, skip_private: bool = False):
    """
    自动扫描模块中所有顶层函数并添加 trace 包装。

    用法：在模块末尾加一行
        auto_trace_module(sys.modules[__name__])

    Args:
        module: 模块对象，通常用 sys.modules[__name__]
        skip_private: 是否跳过以 _ 开头的私有函数
    """
    for name, obj in list(inspect.getmembers(module)):
        # 只处理函数（同步和异步）
        if not (inspect.isfunction(obj) or inspect.iscoroutinefunction(obj)):
            continue
        # 只包装本模块定义的函数（排除 import 进来的）
        if getattr(obj, "__module__", "") != module.__name__:
            continue
        # 已经被 @trace 或 auto_trace 包装过的跳过
        if getattr(obj, "_trace_wrapped", False):
            continue
        # 跳过私有函数
        if skip_private and name.startswith("_"):
            continue
        wrapped = trace(obj)
        setattr(module, name, wrapped)


def auto_trace_cls(cls):
    """
    类装饰器：自动给类中所有方法添加 trace 包装。

    用法：
        @auto_trace_cls
        class MyClass:
            ...
    """
    for name, obj in list(cls.__dict__.items()):
        if name.startswith("__") and name.endswith("__"):
            continue
        if getattr(obj, "_trace_wrapped", False):
            continue
        if inspect.isfunction(obj):
            # 普通实例方法
            wrapped = trace(obj)
            setattr(cls, name, wrapped)
        elif isinstance(obj, staticmethod):
            if getattr(obj.__func__, "_trace_wrapped", False):
                continue
            wrapped = trace(obj.__func__)
            setattr(cls, name, staticmethod(wrapped))
        elif isinstance(obj, classmethod):
            if getattr(obj.__func__, "_trace_wrapped", False):
                continue
            wrapped = trace(obj.__func__)
            setattr(cls, name, classmethod(wrapped))
        # property / 其他 descriptor 跳过
    return cls


def enable_trace():
    global TRACE_ENABLED
    TRACE_ENABLED = True


def disable_trace():
    global TRACE_ENABLED
    TRACE_ENABLED = False
