"""页面脚本取值的统一封装（阶段 1 · R2）：把 `evaluate_js` 的返回值解析成 Python 对象。

⚠️ 为什么必须有这一层 —— Qt 后端的 `Window.evaluate_js()` **返回 str、不做 JSON 解析**
   （阶段 0 实测 A14：`type(result) == str`）。若在调用处直接写 `result["x"]`，
   在 Qt 下就变成「对字符串取下标」→ 要么抛异常、要么静默取到一个字符。
   后者是典型的**静默出错**：页面明明是对的，代码却拿到错的东西。

⚠️ R1 硬约束：本模块的函数**必须在主线程调用**。
   pywebview 的 `after_start` 回调跑在工作线程（实测 Thread-2），在那里直接调
   `evaluate_js` / 碰 Qt 控件会**挂死**（不是报错，是没有任何输出地卡住）。
   需要从工作线程取值时，走 `Window.evaluate_js()` 这类**已做线程投递**的入口，
   或自建 `QObject + Signal` 桥把取值动作投到主线程。

设计取舍：
- **非 str 一律原样返回**（后端若已解析过 JSON，就不要再解析一次）；
  ⚠️ 因此 `None` 有两种可能（JS 返回 null / 后端把 undefined 落成 None），无法在这里区分 ——
  需要区分时请让脚本显式返回 `{ok:false}` 这类结构，而不是靠 None。
- **空串与非法 JSON 默认抛 `JsValueError`**，不静默变 None：
  「脚本写错」必须表现成错误，不能表现成「页面上没有这个元素」。
  确实要兜底的地方，显式传 `default=`，并会在 stdout 打一条警告（仍然可见）。
"""
import json

__all__ = ["JsValueError", "eval_js_json"]


class JsValueError(RuntimeError):
    """JS 脚本返回了「无法解析成 Python 对象」的东西（空串 / 非法 JSON）。"""


_UNSET = object()


def _fallback(default, why, script):
    if default is _UNSET:
        raise JsValueError("%s；script=%r" % (why, script[:160]))
    print("[webview_js] ⚠️ %s → 用 default 兜底；script=%r" % (why, script[:160]))
    return default


def eval_js_json(window, script, *, default=_UNSET):
    """在当前页面执行 `script`，把结果解析成 Python 对象。

    :param window: pywebview 的窗口对象（需有 `evaluate_js`），**须在主线程**调用
    :param script: JS 表达式；建议以 `JSON.stringify(...)` 收尾，语义最稳
    :param default: 解析失败时的兜底值；不传则解析失败抛 `JsValueError`
    :raises JsValueError: 返回空串 / 非法 JSON 且未给 `default`
    """
    raw = window.evaluate_js(script)
    if not isinstance(raw, str):
        return raw                      # 后端已解析（非 Qt 形态）→ 原样返回，不再解析
    s = raw.strip()
    if s == "":
        return _fallback(default, "脚本返回空串（JS 的 undefined / 空返回）", script)
    try:
        return json.loads(s)
    except ValueError as e:
        return _fallback(default, "返回值不是合法 JSON（%s）" % e, script)
