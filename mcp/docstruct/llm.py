"""Schema-driven extraction over the local vLLM chat endpoint. qwen-3.6-chat is a REASONING model:
it spends tokens thinking before answering, so max_tokens must be generous or content comes back
empty with finish_reason=length -- extract() turns that into a clear error instead of a crash."""
import json
import urllib.request

TEMPLATES = {
    "cht_bill": ('{"公司":"","期別":"","繳費總金額":0,"繳費方式":"","發票號碼":"","隨機碼":"",'
                 '"營運處代號":"","用戶號碼":"","用戶帳號":"","計費期間":"",'
                 '"費用項目":[{"項目":"","金額":0}]}'),
}


def strip_fences(s):
    s = (s or "").strip()
    if s.startswith("```"):
        parts = s.split("```")
        if len(parts) >= 2:
            s = parts[1]
            if s.lower().startswith("json"):
                s = s[4:]
    return s.strip()


def build_prompt(text, schema):
    return (
        "以下是從文件抽取（可能經 OCR，含簡繁混字或雜訊）的全文。"
        "請依照給定格式抽取欄位，只輸出 JSON（不要任何其他文字或說明），"
        "所有中文一律正規化為繁體中文，數字使用半形：\n"
        "格式：\n%s\n\n全文：\n%s" % (schema, text)
    )


def call_llm(cfg, prompt):
    """POST /v1/chat/completions -> (content|None, finish_reason). The only network touchpoint."""
    body = {"model": cfg["llm_model"], "temperature": 0, "max_tokens": cfg["max_tokens"],
            "messages": [{"role": "user", "content": prompt}]}
    req = urllib.request.Request(cfg["llm_base_url"].rstrip("/") + "/v1/chat/completions",
                                 data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    resp = urllib.request.urlopen(req, timeout=cfg["llm_timeout_seconds"])
    out = json.loads(resp.read())
    choice = out["choices"][0]
    return choice["message"].get("content"), choice.get("finish_reason", "")


def extract(cfg, text, schema):
    prompt = build_prompt(text, schema)
    content, finish = call_llm(cfg, prompt)
    if not content:
        if finish == "length":
            return {"error": "LLM ran out of tokens while reasoning (finish_reason=length); "
                             "raise max_tokens (config key max_tokens / DOCSTRUCT_MCP_MAX_TOKENS)"}
        return {"error": "LLM returned empty content (finish_reason=%s)" % finish}
    raw = strip_fences(content)
    try:
        return {"fields": json.loads(raw)}
    except ValueError as e:
        retry_prompt = prompt + ("\n\n注意：你上一次的輸出不是合法 JSON（%s）。"
                                 "請重新只輸出合法 JSON。" % e)
        content2, _ = call_llm(cfg, retry_prompt)
        raw2 = strip_fences(content2 or "")
        try:
            return {"fields": json.loads(raw2)}
        except ValueError:
            return {"error": "LLM output is not valid JSON after one retry", "raw": raw2 or raw}
