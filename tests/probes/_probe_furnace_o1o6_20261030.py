# -*- coding: utf-8 -*-
"""O1/O2/O3/O5/O6 + 看板口径 验收探针（2026-10-30）

J 系：
  J1 列表瘦身：无 persona 全文、只回 persona_head，卡片所需字段齐全
  J2 长人格截断真的生效：临时造一个 4000 字人格的应用 → 列表里 ≤300 字 → 删掉
     （⚠️ 只确认「截断」不确认字面：要靠真实长数据，不能靠断言常量）
  J3 版本列表不再下发 snapshot
  J4 看板口径：hit+miss==answered，hit_rate=hit/answered，failed 单列
  J5 nudge 60s 冷却：连打两次，第二次必须 throttled
  J6 版本上限：静态断言（造 31 条版本成本过高，不做实测，如实标 SKIP）
"""
import json
import pathlib
import sys
import time
import urllib.error
import urllib.request

API = "http://127.0.0.1:8000"
WS = pathlib.Path(__file__).resolve().parents[3]   # tests/probes/x.py → 工作区根
BACKEND = WS / "ai-study-companion" / "backend"
APP_ID = 1
RESULTS = []
_op = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def report(rid, ok, detail=""):
    RESULTS.append((rid, "PASS" if ok else "FAIL", str(detail)))
    print(("[PASS] " if ok else "[FAIL] ") + rid + "  " + str(detail)[:190], flush=True)


def skip(rid, why):
    RESULTS.append((rid, "SKIP", why))
    print("[SKIP] " + rid + "  " + why, flush=True)


def call(method, path, body=None, timeout=90):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(API + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with _op.open(req, timeout=timeout) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else None), len(raw)
    except urllib.error.HTTPError as e:
        return e.code, e.read()[:200].decode("utf-8", "replace"), 0


def wait_ready(limit=180):
    t0 = time.time()
    while time.time() - t0 < limit:
        try:
            st, _, _ = call("GET", "/api/health", timeout=5)
            if st == 200:
                return True
        except Exception:
            pass
        time.sleep(2)
    return False


def main():
    if not wait_ready():
        print("后端未就绪")
        sys.exit(2)

    # J1 列表投影
    st, apps, nbytes = call("GET", "/api/apps")
    ok_shape = st == 200 and isinstance(apps, list) and apps
    has_persona_full = any("persona" in a for a in (apps or []))
    has_head = all(("persona_head" in a) for a in (apps or [])) if ok_shape else False
    need = {"id", "name", "spirit", "status", "material_count", "session_count", "updated_at"}
    missing = [sorted(need - set(a)) for a in (apps or [])][:2]
    report("J1 列表瘦身（persona_head，无全文，卡片字段齐全）",
           ok_shape and has_head and not has_persona_full and not any(missing),
           f"st={st} 条数={len(apps or [])} 字节={nbytes} 有全文={has_persona_full} 缺字段={missing}")

    # J2 长人格截断（造一个临时应用，验完删）
    long_persona = "人格测试" * 800          # 3200 字
    tmp_id = None
    try:
        st, made, _ = call("POST", "/api/apps", {"name": "探针临时应用", "persona": long_persona,
                                                 "material_ids": [43]})
        if st not in (200, 201) or not isinstance(made, dict) or not made.get("id"):
            skip("J2 长人格在列表被截断", f"临时应用创建失败 st={st} {str(made)[:80]}")
        else:
            tmp_id = made["id"]
            st2, apps2, n2 = call("GET", "/api/apps")
            row = next((a for a in apps2 if a["id"] == tmp_id), None)
            head_len = len((row or {}).get("persona_head") or "")
            report("J2 长人格在列表被截断", row is not None and head_len <= 300,
                   f"临时应用 persona 原文 {len(long_persona)} 字 → 列表 persona_head {head_len} 字")
    finally:
        if tmp_id:
            call("DELETE", f"/api/apps/{tmp_id}")

    # J3 版本列表无 snapshot
    st, vers, nbytes = call("GET", f"/api/apps/{APP_ID}/versions")
    rows = vers if isinstance(vers, list) else []
    no_snap = all("snapshot" not in v for v in rows)
    report("J3 版本列表不下发 snapshot", st == 200 and no_snap,
           f"st={st} 版本数={len(rows)} 字节={nbytes} 含 snapshot={not no_snap}")

    # J4 看板口径自洽
    st, s, _ = call("GET", f"/api/apps/{APP_ID}/stats")
    ok_keys = isinstance(s, dict) and {"hit_count", "miss_count", "answered_count", "failed_count", "hit_rate"} <= set(s)
    if ok_keys:
        consistent = (s["hit_count"] + s["miss_count"]) == s["answered_count"]
        expect_rate = round(s["hit_count"] / s["answered_count"], 2) if s["answered_count"] else None
        report("J4 看板口径自洽（分母=真正回答数）",
               consistent and s["hit_rate"] == expect_rate,
               f"hit={s['hit_count']} miss={s['miss_count']} answered={s['answered_count']} "
               f"failed={s['failed_count']} rate={s['hit_rate']} 预期={expect_rate}")
    else:
        report("J4 看板口径自洽（分母=真正回答数）", False, f"字段缺失：{sorted(s) if isinstance(s, dict) else s}")

    # J5 nudge 冷却
    body = {"session_id": None}
    st1, r1, _ = call("POST", f"/api/apps/{APP_ID}/nudge", body)
    st2, r2, _ = call("POST", f"/api/apps/{APP_ID}/nudge", body)
    second_throttled = isinstance(r2, dict) and (r2.get("throttled") or not r2.get("text"))
    report("J5 nudge 60s 冷却（第二次静默）", st2 == 200 and second_throttled,
           f"第一次 st={st1} 有文案={bool(isinstance(r1, dict) and r1.get('text'))}；"
           f"第二次 st={st2} throttled={isinstance(r2, dict) and r2.get('throttled')}")

    # J6 版本上限：静态
    svc = (BACKEND / "app/services/apps.py").read_text(encoding="utf-8")
    static_ok = "VERSION_KEEP = 30" in svc and "if len(olds) > VERSION_KEEP + 1:" in svc
    if static_ok:
        skip("J6 版本上限 30 条（未实测）", "代码已就位；造 31 条版本会污染真实应用，未做端到端")
    else:
        report("J6 版本上限 30 条（未实测）", False, "静态断言未通过")

    n_pass = sum(1 for _, s2, _ in RESULTS if s2 == "PASS")
    n_fail = sum(1 for _, s2, _ in RESULTS if s2 == "FAIL")
    n_skip = sum(1 for _, s2, _ in RESULTS if s2 == "SKIP")
    print("\n" + "=" * 60)
    print(f"总计 {len(RESULTS)} 项：PASS {n_pass} / FAIL {n_fail} / SKIP {n_skip}")
    sys.exit(1 if n_fail else 0)


if __name__ == "__main__":
    main()
